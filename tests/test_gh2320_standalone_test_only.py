"""gh-2320: a standalone object's ``test_only`` dependency reaches its test.

gh-537's ``{ name = "x", test_only = true }`` links ``x_core`` into the
declaring component's C test and bench and nothing else. A module object
got that; a standalone one got nothing, because its test and bench lines
shared the ``.so``'s link block, which ``dep_link_libs`` filters every
``test_only`` entry out of -- so a test round-tripping through the sibling
(gh-537's own motivating case) could not link.

The test and bench now read their own slot, ``test_link_libs_block``: the
``.so``'s libraries plus what the ``test_only`` edges reach. Checked on the
rendered CMakeLists, call by call: the test and bench link ``wtr_core`` and
what ``wtr`` itself links; the extension target links neither.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C


def _call(cmake: str, target: str) -> str:
    """The ``target_link_libraries(<target> ...)`` call, or ``""``."""
    m = re.search(rf"target_link_libraries\({target}\s[^)]*\)", cmake)
    return m.group(0) if m else ""


@pytest.fixture
def cmake(tmp_path: Path) -> str:
    """Standalone ``rdr`` test-only-depends on ``wtr``, which links ``src``."""
    assert run_cli("new", "demo", cwd=tmp_path).returncode == 0
    root = tmp_path / "demo"
    for obj in ("src", "wtr", "rdr"):
        r = run_cli("object", obj, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    cfg = C.load(root)
    cfg["wtr"]["depends_on"] = [{"name": "src", "link": True}]
    cfg["rdr"]["depends_on"] = [{"name": "wtr", "test_only": True}]
    C.save(root, cfg)
    # apply is additive: remove the file under test to force a re-render.
    path = root / "native" / "src" / "rdr" / "CMakeLists.txt"
    path.unlink()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("target", ["test_rdr_core", "bench_rdr_core"])
def test_the_test_and_bench_link_it(cmake: str, target: str) -> None:
    call = _call(cmake, target)
    assert call, cmake
    assert "wtr_core" in call and "src_core" in call, call


def test_the_extension_does_not(cmake: str) -> None:
    call = _call(cmake, "rdr")
    assert call, cmake
    assert "wtr_core" not in call and "src_core" not in call, call
    assert "wtr_core" not in cmake.split("add_executable", 1)[0], cmake
