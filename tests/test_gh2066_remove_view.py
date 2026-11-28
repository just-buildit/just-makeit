"""gh-2066: `jm remove` takes out a view, and a view's own members.

`jm view` adds a view, and `jm method|property|warning|error --view` give it
members of its own, but `jm remove` had no `view` kind and no `--view`: a
view or its method could only be taken out by editing the manifest. gh-2055's
type-change refusal had no route to print when a speaker was a view's
method, and said so.

Now `jm remove view <ClassName> --object <obj>` removes the view and its
binding fragment, and `--view <ClassName>` on each kind a view declares of
its own (`_remove.VIEW_KINDS`) removes that member and leaves the parent's.

GATE: for every kind in `_remove.VIEW_KINDS` (a kind without a case fails),
      adding the member to a view and removing it with `--view` leaves
      `status --check` at 0 and the view's manifest entry without it, while
      the parent's same-named member stays; `remove view` of a view with
      one of each, in its parent's module or in another (gh-2516), leaves
      no fragment, no class in the stub or `__init__.py`, and
      `status --check` at 0; and `--view` naming no view, or on a kind a
      view has none of, is refused with the tree unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _remove

_M = ("--module", "m")

#: kind -> (the add, with `--view V`; the name `jm remove <kind>` takes; the
#: view's manifest list it lands in; the parent's same member, which the
#: remove must leave). Held to `_remove.VIEW_KINDS`.
CASES = {
    "method": (
        ("method", "o", "vm", *_M, "--view", "V", "--arg-type", "double",
         "--return-type", "double"),
        "vm", "methods",
        ("method", "o", "vm", *_M, "--arg-type", "double",
         "--return-type", "double"),
    ),
    "property": (
        ("property", "o", "vp", "--type", "double", "--view", "V", *_M),
        "vp", "properties",
        ("property", "o", "vp", "--type", "double", *_M),
    ),
    "warning": (
        ("warning", "o", "--condition", "g2", "--message", "hot",
         "--view", "V", *_M),
        "g2", "warnings",
        ("warning", "o", "--condition", "g2", "--message", "hot", *_M),
    ),
    "error": (
        ("error", "o", "--category", "ValueError", "--message", "bad",
         "--view", "V", *_M),
        "o", "create_error",
        ("error", "o", "--category", "KeyError", "--message", "worse",
         *_M),
    ),
}  # fmt: skip


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _clean(root: Path) -> None:
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout


def _project(tmp_path: Path, *steps: tuple) -> Path:
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "module", "m")
    _ok(root, "object", "o", *_M)
    _ok(root, "view", "o", "V", *_M, "--create-fn", "o_create_v")
    for argv in steps:
        _ok(root, *argv)
    _clean(root)
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_every_view_kind_has_a_case():
    assert set(CASES) == set(_remove.VIEW_KINDS)


@pytest.mark.parametrize("kind", sorted(CASES))
def test_a_views_member_comes_out_and_the_parents_stays(tmp_path, kind):
    add, name, key, parents = CASES[kind]
    root = _project(tmp_path, parents, add)
    (view,) = C.views(C.load(root), "o")
    assert key in view, "not vacuous: the view holds the member"

    _ok(root, "remove", kind, name, "--object", "o", "--view", "V",
        "--force")  # fmt: skip

    _clean(root)
    cfg = C.load(root)
    (view,) = C.views(cfg, "o")
    assert key not in view, view
    own = cfg["o"].get(key)
    assert own, f"the parent's {kind} went too: {cfg['o']}"


@pytest.mark.parametrize("where", ["home", "another-module"])
def test_a_view_comes_out_whole(tmp_path, where):
    module = "m" if where == "home" else "n"
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "module", "m")
    _ok(root, "object", "o", *_M)
    if module != "m":
        _ok(root, "module", module)
        _ok(root, "object", "q", "--module", module)
    _ok(root, "view", "o", "V", "--module", module, "--create-fn", "o_mk_v")
    for kind, (add, *_rest) in sorted(CASES.items()):
        _ok(root, *add)
    _clean(root)
    pkg = root / "src" / "p" / module
    frags = list((root / "native").rglob("*_ext_v.c"))
    assert len(frags) == 1, frags
    assert "class V" in (pkg / f"{module}.pyi").read_text(encoding="utf-8")

    out = _ok(root, "remove", "view", "V", "--object", "o", "--force")

    _clean(root)
    assert not frags[0].exists()
    assert "class V" not in (pkg / f"{module}.pyi").read_text(encoding="utf-8")
    assert "V" not in (pkg / "__init__.py").read_text(encoding="utf-8")
    assert not C.views(C.load(root), "o")
    # The sacred C stays, and the note names it: the constructor, and the
    # function the view's own method bound.
    assert "o_mk_v()" in out and "p_o_vm()" in out, out


@pytest.mark.parametrize(
    "argv",
    [
        ("method", "vm", "--object", "o", "--view", "W"),
        ("view", "W", "--object", "o"),
        ("state", "x", "--object", "o", "--view", "V"),
    ],
    ids=["no-such-view", "remove-no-such-view", "a-kind-a-view-lacks"],
)
def test_a_view_that_is_not_there_is_refused(tmp_path, argv):
    # A state field `x` exists, so only the `--view` check refuses the
    # third: a kind a view declares none of.
    root = _project(
        tmp_path,
        CASES["method"][0],
        ("add", "--object", "o", "--state", "x:double:0", "--force"),
    )
    before = _tree(root)
    r = run_cli("remove", *argv, "--force", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stderr.startswith("error: "), r.stderr
    assert _tree(root) == before
