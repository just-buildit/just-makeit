"""gh-1978: a `jm remove` leaves the tree its manifest describes.

`jm remove method m --object o` took the method out of the manifest, the
binding and the stub, and left ``native/tests/test_o_symbols.c`` -- the
gh-1361 link-check table -- still taking the address of ``p_o_m``. So
`status --check` failed STALE on the tree the remove had just written. It
was also a build break waiting to happen: the remove ends by telling the
author to delete ``p_o_m()`` from ``o_core.c`` by hand, and doing that made
the C test fail to link (``undefined reference to `p_o_m'``), because the
stale table still named it.

The cause was a second copy of the standalone re-render in `_remove`, which
wrote the binding and the stub and never the table. It now goes through
`_glue.regenerate_standalone`, the call `jm property`, `jm warning` and
`jm error` re-render through on the way in. A module object was never
affected: `_regenerate_module` already refreshed every member's table.

The same sweep found the gh-1404 element contract
(``test_<obj>_invariants.py``, which `apply` rewrites) outliving its pair:
on `jm remove method` of the pair's writer or reader, and on
`jm remove object`, where the orphan imported the removed class and the
project's Python tests failed to collect -- on a tree `status --check`
called clean, because status compares the files `apply` writes and `apply`
has no component left to write that one for. Hence the second oracle below,
which does not go through status at all.

GATE: for every kind `jm remove` dispatches (`_cli_remove._KINDS`; a kind
      without a case here fails), adding a member then removing it leaves
      `status --check` at exit 0 and leaves behind no file `apply` rewrites
      (`_createonly.REWRITTEN`) that the add brought; and a project whose
      author followed the remove's "delete it by hand" note builds, links
      and passes `jm test`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jm_stub import drop_jm_stubs
from _jmrun import run_cli
from just_makeit import _cli_remove
from just_makeit import _createonly as CO
from just_makeit import _textio

_M = ("--module", "mod")


def _writer(obj: str = "o", *module: str) -> "list[tuple[str, ...]]":
    """An object with a declared element and the WRITER of it.

    One face only, so there is no contract yet: `_invariants.pairs` needs a
    writer and a reader of one element.
    """
    return [
        ("object", obj, "--arg-type", "float _Complex",
         "--return-type", "float _Complex", *module),
        ("record", obj, "sample", "--type", "float _Complex"),
        ("method", obj, "write", "--arg-type", "sample[]",
         "--return-type", "bool", *module),
    ]  # fmt: skip


def _reader(obj: str = "o", *module: str) -> "tuple[str, ...]":
    """The READER, which brings the contract into existence."""
    return (
        "method", obj, "wait", "--arg-type", "void", "--return-type",
        "sample", "--borrow", "--param", "n:size_t", *module,
    )  # fmt: skip


#: kind -> flavor -> (setup, add, what follows `jm remove <kind>`).
#:
#: Keyed by `_cli_remove._KINDS`, and held to it by
#: `test_every_remove_kind_has_a_case`: the dispatch is the list. A standalone
#: and a module flavor wherever the remove branches on it.
CASES: "dict[str, dict[str, tuple[list, list, tuple]]]" = {
    "method": {
        "standalone": (
            [("object", "o")],
            [("method", "o", "m")],
            ("m", "--object", "o"),
        ),
        "module": (
            [("module", "mod"), ("object", "o", *_M)],
            [("method", "o", "m", *_M)],
            ("m", "--object", "o"),
        ),
        # Removing the reader ends the contract `jm method` wrote.
        "ends-a-pair": (_writer(), [_reader()], ("wait", "--object", "o")),
        # `apply` in the add: `jm method` on a MODULE object does not write
        # the contract itself (`status --check` reports it MISSING right
        # after the add -- an add-side gap, not this fix, reported on the
        # gh-1978 PR), so this brings the tree to what the add should have
        # left.
        "ends-a-pair-in-a-module": (
            [("module", "mod"), *_writer("o", *_M)],
            [_reader("o", *_M), ("apply",)],
            ("wait", "--object", "o"),
        ),
    },
    "property": {
        "standalone": (
            [("object", "o")],
            [("property", "o", "lvl", "--type", "double")],
            ("lvl", "--object", "o"),
        ),
        "module": (
            [("module", "mod"), ("object", "o", *_M)],
            [("property", "o", "lvl", *_M, "--type", "double")],
            ("lvl", "--object", "o"),
        ),
    },
    "warning": {
        "standalone": (
            [("object", "o")],
            [("warning", "o", "--condition", "gain", "--message", "hot")],
            ("gain", "--object", "o"),
        ),
        "module": (
            [("module", "mod"), ("object", "o", *_M)],
            [("warning", "o", *_M, "--condition", "gain",
              "--message", "hot")],
            ("gain", "--object", "o"),
        ),
    },
    "error": {
        "standalone": (
            [("object", "o")],
            [("error", "o", "--category", "ValueError",
              "--message", "bad")],
            ("o", "--object", "o"),
        ),
        "module": (
            [("module", "mod"), ("object", "o", *_M)],
            [("error", "o", *_M, "--category", "ValueError",
              "--message", "bad")],
            ("o", "--object", "o"),
        ),
    },
    "state": {
        "standalone": (
            [("object", "o")],
            [("add", "--object", "o", "--state", "x:double:0", "--force")],
            ("x", "--object", "o"),
        ),
        "module": (
            [("module", "mod"), ("object", "o", *_M)],
            [("add", "--object", "o", "--state", "x:double:0", "--force")],
            ("x", "--object", "o"),
        ),
    },
    "function": {
        "not-last": (
            [("module", "mod"),
             ("function", "g", *_M, "--param", "x:double",
              "--return-type", "double")],
            [("function", "f", *_M, "--param", "x:double",
              "--return-type", "double")],
            ("f", "--module", "mod"),
        ),
        # gh-1479's shape: the last function ends the module core.
        "last": (
            [("module", "mod"), ("object", "o", *_M)],
            [("function", "f", *_M, "--param", "x:double",
              "--return-type", "double")],
            ("f", "--module", "mod"),
        ),
    },
    "object": {
        "standalone": ([("object", "a")], [("object", "o")], ("o",)),
        "module": (
            [("module", "mod"), ("object", "a", *_M)],
            [("object", "o", *_M)],
            ("o",),
        ),
        "with-a-pair": (
            [("object", "a")],
            [*_writer(), _reader()],
            ("o",),
        ),
    },
    "module": {
        "module": (
            [("object", "a")],
            [("module", "mod"), ("object", "o", *_M)],
            ("mod",),
        ),
    },
}  # fmt: skip


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _rewritten(root: Path) -> "set[str]":
    """The project's files that `apply` rewrites wholesale -- jm's.

    Read from jm's own classification (`_createonly`), not a path list, so a
    new derived file is covered the day it is classified.
    """
    out = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        rule = CO.classify(rel, root)
        if rule is not None and rule.kind in CO.REWRITTEN:
            out.add(rel)
    return out


def test_every_remove_kind_has_a_case():
    assert set(CASES) == set(_cli_remove._KINDS)


@pytest.mark.parametrize(
    "kind, flavor",
    [(k, f) for k, flavors in CASES.items() for f in flavors],
    ids=[f"{k}-{f}" for k, flavors in CASES.items() for f in flavors],
)
def test_add_then_remove_leaves_the_tree_in_sync(tmp_path, kind, flavor):
    setup, add, rest = CASES[kind][flavor]
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    for argv in setup:
        _ok(root, *argv)
    before = _rewritten(root)
    for argv in add:
        _ok(root, *argv)
    # Not vacuous: the add left a clean tree, so a red one below is the
    # remove's.
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, f"the ADD left status red:\n{s.stdout}"

    _ok(root, "remove", kind, *rest, "--force")

    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, s.stdout
    left = sorted(_rewritten(root) - before)
    assert not left, f"jm's own files outlived the remove: {left}"


def test_following_the_remove_note_still_builds(tmp_path):
    """The independent oracle: the compiler, the linker and the generated
    tests, through `jm test`.

    Two removes, each the author's ordinary next step: a method whose body
    the author then deletes as the remove's note says -- the stale table
    took its address, so the C test stopped linking -- and an object with
    an element contract, whose orphaned contract test imported the removed
    class.
    """
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    for argv in [
        ("object", "a"),
        ("method", "a", "m"),
        *_writer("x"),
        _reader("x"),
    ]:
        _ok(root, *argv)

    out = _ok(root, "remove", "method", "m", "--object", "a", "--force")
    note = re.search(r"note: (\w+)\(\) remains in (\w+\.c)", out)
    assert note, out
    sym, fname = note.groups()
    core = root / "native" / "src" / "a" / fname
    text = drop_jm_stubs(core.read_text(encoding="utf-8"), "m")
    assert sym not in text, text
    _textio.write_text(core, text)

    _ok(root, "remove", "object", "x", "--force")

    r = run_cli("test", cwd=root)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
