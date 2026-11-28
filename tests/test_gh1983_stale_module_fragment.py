"""gh-1983, gh-2461: a module member's binding fragment does not outlive it.

`jm remove object <o>` on a MODULE object left its binding fragment
``native/src/<cname>/<cname>_ext_<o>.c`` behind (gh-2461: in every layout),
still including the header the remove had just deleted, while
`status --check` exited 0. A re-add of the name then kept the stale
fragment -- a fragment is sacred, so jm keeps one it finds -- and when the
old object had a method the build failed with an implicit declaration of
the old method's C, on a tree `status --check` still called clean.

gh-2516 made the remove delete the fragment (through
`_linkcheck.binding_sources`, every view's too). A project whose member
went by an older jm, or by a hand edit of the manifest, still carries one,
so the other two halves are here: `status` reports a fragment no member
owns as ORPHAN (`_object.orphan_fragments`), and adding a member over one
is refused, naming the file (`_object.refuse_stale_fragment`, gh-2459's
rule for a component's own directory).

GATE: in every layout and module spelling, removing a module object leaves
      no file under ``native/`` naming its header or its fragment, and
      `status --check` at 0; a fragment put back is ORPHAN, fails
      `status --check`, is in ``--json``, and refuses a re-add of the
      object -- or of a view of that name -- with the tree unchanged; an
      author's own ``<cname>_ext_*.c`` that a hook includes is not an
      orphan; and add, method, remove, re-add builds and passes `jm test`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from _compilers import default_cc
from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _incpath as INC
from just_makeit import _textio

LAYOUTS = ("flat", "nested", "mirrored")
MODULES = ("m", "dsp.m")


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _project(tmp_path: Path, layout: str, module: str) -> Path:
    """Two objects in *module*, the first with a method of its own."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    if layout != "flat":
        cfg = C.load(root)
        cfg["project"]["layout"] = layout
        C.save(root, cfg)
    _ok(root, "module", module)
    _ok(root, "object", "o", "--module", module)
    _ok(root, "object", "k", "--module", module)
    _ok(root, "method", "o", "run", "--module", module)
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, f"the SETUP left status red:\n{s.stdout}"
    return root


def _fragment(root: Path, module: str, frag_id: str) -> Path:
    cfg = C.load(root)
    cname = C.module_paths(module).cname
    return INC.src_path(root, cname, cfg) / f"{cname}_ext_{frag_id}.c"


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_remove_leaves_no_fragment(tmp_path, layout, module):
    root = _project(tmp_path, layout, module)
    frag = _fragment(root, module, "o")
    assert frag.is_file(), "not vacuous: the fragment was written"

    _ok(root, "remove", "object", "o", "--force")

    assert not frag.exists()
    named = [
        p.relative_to(root).as_posix()
        for p in (root / "native").rglob("*")
        if p.is_file()
        and (
            "o_core.h" in p.read_text(encoding="utf-8", errors="replace")
            or p.name == frag.name
        )
    ]
    assert not named, named
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout


@pytest.mark.parametrize("module", MODULES)
def test_a_left_fragment_is_an_orphan_and_refuses_a_readd(tmp_path, module):
    """What an older jm left: the remove's tree, the fragment put back."""
    root = _project(tmp_path, "flat", module)
    frag = _fragment(root, module, "o")
    stale = frag.read_bytes()
    _ok(root, "remove", "object", "o", "--force")
    frag.write_bytes(stale)
    rel = frag.relative_to(root).as_posix()

    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 1, s.stdout
    assert "ORPHAN (1)" in s.stdout and f"! {rel}" in s.stdout, s.stdout
    j = run_cli("status", "--json", cwd=root)
    assert json.loads(j.stdout)["orphan_fragments"] == [rel], j.stdout

    before = _tree(root)
    r = run_cli("object", "o", "--module", module, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert rel in r.stderr and "gh-1983" in r.stderr, r.stderr
    assert _tree(root) == before, "a refusal wrote to the tree"

    frag.unlink()
    _ok(root, "object", "o", "--module", module)
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout


def test_a_left_view_fragment_refuses_the_view(tmp_path):
    """A view's fragment is the same file shape, so the same rule."""
    root = _project(tmp_path, "flat", "m")
    _ok(root, "view", "o", "Alt", "--module", "m", "--create-fn", "alt_mk")
    frag = _fragment(root, "m", "alt")
    stale = frag.read_bytes()
    _ok(root, "remove", "view", "Alt", "--object", "o", "--force")
    assert not frag.exists()
    frag.write_bytes(stale)

    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 1 and "ORPHAN (1)" in s.stdout, s.stdout
    before = _tree(root)
    r = run_cli(
        "view", "o", "Alt", "--module", "m", "--create-fn", "alt_mk2",
        cwd=root,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert frag.relative_to(root).as_posix() in r.stderr, r.stderr
    assert _tree(root) == before


def test_an_included_fragment_is_not_an_orphan(tmp_path):
    """An author's own file of a fragment's shape, compiled through a hook,
    is theirs and built: nothing to report."""
    root = _project(tmp_path, "flat", "m")
    d = _fragment(root, "m", "o").parent
    _textio.write_text(d / "m_ext_helpers.c", "/* the author's */\n")
    _textio.write_text(d / "m_ext_k_extra.c", '#include "m_ext_helpers.c"\n')
    s = run_cli("status", "--check", cwd=root)
    assert "m_ext_helpers.c" not in s.stdout, s.stdout
    assert "ORPHAN" not in s.stdout, s.stdout


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


@pytest.mark.slow
@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_add_remove_readd_builds_and_passes(tmp_path):
    """#1983's own oracle: the compiler, through `jm test`. The old
    object's method is what made the stale fragment fail to build."""
    root = _project(tmp_path, "flat", "m")
    _ok(root, "remove", "object", "o", "--force")
    _ok(root, "object", "o", "--module", "m")
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout
    r = run_cli("test", cwd=root)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
