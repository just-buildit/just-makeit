"""An object's link closure does not depend on declaration order (gh-1549).

GATE: an object's generated link lines are the full depends_on closure, whatever order its components are declared in.

``apply`` replays components one at a time into a scratch tree, and each
render walked the ``depends_on`` graph over the scratch manifest, which held
only what had been replayed so far. With ``b -> a -> c`` and ``a`` replayed
after ``b``, the walk reached ``a`` with no edges yet and stopped there, so
``b_core``, ``test_b_core`` and ``bench_b_core`` all lost ``c_core``. doppler
hit it moving one object into a module declared later: its C tests failed to
link, and ``status --check`` passed, because it observes the same replay.

The property is order-independence, so it is tested as that: one project,
applied with its modules declared in both orders, must produce the same
``b`` CMakeLists, carrying the whole closure on all three link lines. A
same-module case covers the member order inside one module.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402


def _ok(r) -> None:
    assert r.returncode == 0, r.stdout + r.stderr


def _add_dep(proj: Path, comp: str, dep: str) -> None:
    """Declare ``comp`` depends_on ``dep`` (link = true): TOML-only."""
    f = proj / "objects" / f"{comp}.toml"
    t = f.read_text(encoding="utf-8")
    hdr = re.search(rf"^\[{comp}\]\n", t, re.M)
    assert hdr, f"no [{comp}] table in {f}"
    line = f'depends_on = [{{ name = "{dep}", link = true }}]\n'
    f.write_text(t[: hdr.end()] + line + t[hdr.end() :], encoding="utf-8")


def _set_include(proj: Path, include: str) -> None:
    f = proj / "just-makeit.toml"
    t = f.read_text(encoding="utf-8")
    new, n = re.subn(r"^include = .*$", f"include = {include}", t, flags=re.M)
    assert n == 1, "no single `include =` line in just-makeit.toml"
    f.write_text(new, encoding="utf-8")


def _link_lines(proj: Path, comp: str) -> "dict[str, str]":
    """Each of comp's three target_link_libraries(...) blocks, whole."""
    text = (proj / "native/src" / comp / "CMakeLists.txt").read_text()
    out = {}
    for tgt in (f"{comp}_core", f"test_{comp}_core", f"bench_{comp}_core"):
        m = re.search(rf"target_link_libraries\({tgt}\b[^)]*\)", text)
        assert m, f"no target_link_libraries({tgt} ...) in {comp}'s CMake"
        out[tgt] = m.group(0)
    return out


def _cross_module_project(tmp: Path) -> Path:
    """b in m1, a and c in m2; b -> a -> c, both edges link = true."""
    _ok(run_cli("new", "p", cwd=tmp))
    proj = tmp / "p"
    _ok(run_cli("module", "m1", cwd=proj))
    _ok(run_cli("object", "b", "--module", "m1", cwd=proj))
    _ok(run_cli("module", "m2", cwd=proj))
    _ok(run_cli("object", "a", "--module", "m2", cwd=proj))
    _ok(run_cli("object", "c", "--module", "m2", cwd=proj))
    _add_dep(proj, "b", "a")
    _add_dep(proj, "a", "c")
    return proj


def test_the_closure_is_the_same_in_either_module_order(tmp_path):
    proj = _cross_module_project(tmp_path)

    _set_include(
        proj, '["objects/*.toml", "modules/m1.toml", "modules/m2.toml"]'
    )
    _ok(run_cli("apply", cwd=proj))
    later = _link_lines(proj, "b")

    _set_include(
        proj, '["objects/*.toml", "modules/m2.toml", "modules/m1.toml"]'
    )
    _ok(run_cli("apply", cwd=proj))
    earlier = _link_lines(proj, "b")

    for tgt, block in later.items():
        assert "a_core" in block and "c_core" in block, (
            f"{tgt} lacks the closure when the dependency's module is "
            f"declared later:\n{block}"
        )
    assert later == earlier, "b's link lines depend on module order"


@pytest.mark.parametrize("order", [("b", "a", "c"), ("c", "a", "b")])
def test_the_closure_is_whole_within_one_module(tmp_path, order):
    """The same walk over members of ONE module, in both orders."""
    _ok(run_cli("new", "p", cwd=tmp_path))
    proj = tmp_path / "p"
    _ok(run_cli("module", "m", cwd=proj))
    for comp in order:
        _ok(run_cli("object", comp, "--module", "m", cwd=proj))
    _add_dep(proj, "b", "a")
    _add_dep(proj, "a", "c")
    _ok(run_cli("apply", cwd=proj))
    for tgt, block in _link_lines(proj, "b").items():
        assert "c_core" in block, (
            f"{tgt} lost c_core (order {order}):\n{block}"
        )
