"""gh-2318: a ``test_only`` dependency stays its declaring component's alone.

gh-537 kept ``{name = "x", test_only = true}`` off its own component's core
PUBLIC link line, header and ``.so``. But ``transitive_dep_cores`` followed
every edge of every dependency, ``test_only`` ones included, so a component
depending on that component got ``x_core`` on its core's PUBLIC line, test
and bench -- the shipped core linking a library that exists only for a
sibling's C test. Found by doppler: ``awgn`` declared test-only on
``carrier_acq`` landed PUBLIC on ``async_dsss_receiver_core``.

The same walk leaked one level down, too: the PUBLIC line dropped only the
component's OWN test-only cores, so a core the test-only dependency itself
depends on went PUBLIC. Both now read `_config.test_only_dep_cores`, the
closure of every edge minus the closure of the shipped ones.

The graph, all in one module::

    a --test_only--> x --> y
    b ------------> a
    c ------------> a --(real)--> x   (the control: a real edge still walks)
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _q(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _project(tmp_path: Path, a_to_x: dict) -> Path:
    """Objects x, y, a, b in one module; *a_to_x* is a's edge to x."""
    d = tmp_path / "p"
    _q(new_run, "p", d, [], [], modules=["m"])
    for obj in ("y", "x", "a", "b"):
        _q(object_run, d, obj, "m")
    cfg = C.load(d)
    cfg["x"]["depends_on"] = ["y"]
    cfg["a"]["depends_on"] = [a_to_x]
    cfg["b"]["depends_on"] = ["a"]
    C.save(d, cfg)
    # apply is additive: remove what is under test to force a re-render.
    for obj in ("a", "b"):
        (d / "native" / "src" / obj / "CMakeLists.txt").unlink()
    _q(apply_run, d)
    return d


def _cmake(proj: Path, obj: str) -> str:
    return (proj / "native" / "src" / obj / "CMakeLists.txt").read_text()


def _public(cmake: str, obj: str) -> str:
    """The core's PUBLIC ``target_link_libraries`` call, or ``""``."""
    m = re.search(
        rf"target_link_libraries\({obj}_core\s+(?:PUBLIC|INTERFACE)[^)]*\)",
        cmake,
    )
    return m.group(0) if m else ""


@pytest.fixture()
def test_only(tmp_path: Path) -> Path:
    return _project(tmp_path, {"name": "x", "test_only": True})


def test_a_dependent_does_not_link_the_test_only_core(test_only: Path) -> None:
    """The issue: b depends on a, and gets none of a's test fixture."""
    cmake = _cmake(test_only, "b")
    assert "a_core" in _public(cmake, "b"), cmake
    assert "x_core" not in cmake, cmake
    assert "y_core" not in cmake, cmake


def test_a_test_only_cores_own_deps_stay_off_public(test_only: Path) -> None:
    """One level down: a's test links x and what x needs, its core neither."""
    cmake = _cmake(test_only, "a")
    assert "x_core" in cmake and "y_core" in cmake, cmake
    assert "x_core" not in _public(cmake, "a"), cmake
    assert "y_core" not in _public(cmake, "a"), cmake


def test_a_real_edge_still_walks(tmp_path: Path) -> None:
    """The control: with a real edge to x, b's closure reaches x and y."""
    proj = _project(tmp_path, {"name": "x"})
    cmake = _cmake(proj, "b")
    assert "x_core" in _public(cmake, "b"), cmake
    assert "y_core" in _public(cmake, "b"), cmake
    assert "x_core" in _public(_cmake(proj, "a"), "a")


def test_a_collocated_objects_test_only_deps_stay_off_public(
    tmp_path: Path,
) -> None:
    """The object named for its module shares the module's CMakeLists, which
    `_regenerate_module` renders by its own path -- the second PUBLIC-line
    builder, held to the same answer."""
    d = tmp_path / "p"
    _q(new_run, "p", d, [], [], modules=["m"])
    for obj in ("y", "x", "m"):
        _q(object_run, d, obj, "m")
    assert C.collocated_object(C.load(d), "m") == "m"
    cfg = C.load(d)
    cfg["x"]["depends_on"] = ["y"]
    cfg["m"]["depends_on"] = [{"name": "x", "test_only": True}]
    C.save(d, cfg)
    (d / "native" / "src" / "m" / "CMakeLists.txt").unlink()
    _q(apply_run, d)
    cmake = _cmake(d, "m")
    assert "x_core" in cmake and "y_core" in cmake, cmake
    assert "x_core" not in _public(cmake, "m"), cmake
    assert "y_core" not in _public(cmake, "m"), cmake
