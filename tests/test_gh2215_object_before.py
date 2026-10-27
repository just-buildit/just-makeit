"""gh-2215: `jm object --module M --before OBJ` places an object in M's order.

A module's ``objects`` list is its load order (gh-1985): the root
``CMakeLists.txt`` blocks and the umbrella header's includes follow it,
through `_libwiring.order_sections` and `_init.order_umbrella_includes`.
`jm object` could only append, so `jm script` could not rebuild a module
whose manifest lists a consumer before the producer its ``object``
init-param names -- the producer has to exist when the consumer's line
runs. ``--before`` is the general answer: create in any order, place where
declared. These tests drive it through the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _libwiring as W  # noqa: E402
from just_makeit._init import order_umbrella_includes  # noqa: E402


def _ok(cwd: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=cwd)
    assert r.returncode == 0, (argv, r.stdout + r.stderr)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Module ``m`` holding ``a`` then ``b``, and module ``n`` holding ``c``."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "module", "m")
    _ok(root, "module", "n")
    for obj, mod in (("a", "m"), ("b", "m"), ("c", "n")):
        _ok(root, "object", obj, "--module", mod)
    return root


def test_it_inserts_ahead_of_the_named_member(project: Path) -> None:
    _ok(project, "object", "x", "--module", "m", "--before", "b")
    assert C.module_objects(C.load(project), "m") == ["a", "x", "b"]


def test_the_wiring_follows_the_gh1985_sort(project: Path) -> None:
    """The root CMakeLists and the umbrella are already in the order
    gh-1985's sorts give for the new list -- so sorting them again is a
    no-op -- and that order puts `x` where the list does."""
    _ok(project, "object", "x", "--module", "m", "--before", "b")
    cfg = C.load(project)
    cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")
    umbrella_path = next((project / "native" / "inc").rglob("p.h"))
    umbrella = umbrella_path.read_text(encoding="utf-8")
    assert W.order_sections(cmake, cfg) == cmake
    assert order_umbrella_includes(umbrella, cfg) == umbrella
    # Not vacuous: the sorted order is the placed one, not the append one.
    blocks = [
        line
        for line in cmake.splitlines()
        if line.startswith("add_subdirectory(native/src/")
    ]
    assert (
        blocks.index("add_subdirectory(native/src/b)")
        < blocks.index("add_subdirectory(native/src/x)")
        < blocks.index("add_subdirectory(native/src/a)")
    )
    assert (
        umbrella.index("p/a/a_core.h")
        < umbrella.index("p/x/x_core.h")
        < umbrella.index("p/b/b_core.h")
    )
    assert run_cli("status", "--check", cwd=project).returncode == 0


@pytest.mark.parametrize(
    "argv, says",
    [
        (("--before", "a"), "needs --module"),
        (("--module", "m", "--before", "zz"), "has no object 'zz'"),
        (("--module", "m", "--before", "c"), "'c' is in module 'n'"),
    ],
    ids=["no-module", "unknown", "other-module"],
)
def test_it_is_refused_before_anything_is_written(
    project: Path, argv: "tuple[str, ...]", says: str
) -> None:
    manifest = C.load(project)
    r = run_cli("object", "x", *argv, cwd=project)
    assert r.returncode != 0
    assert says in r.stderr, r.stderr
    assert "Traceback" not in r.stderr
    assert C.load(project) == manifest
    assert not (project / "native" / "src" / "x").exists()
