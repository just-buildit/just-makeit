"""gh-1987: the C benchmark is the author's, and no member verb rewrites it.

``native/benchmarks/bench_<obj>_core.c`` is classified AUTHOR
(`_createonly`: "the author's C bench"), `apply` never rewrites it, and
`jm property` / `jm warning` / `jm error` leave it alone. But on a
STANDALONE object `jm method` re-rendered it wholesale, and so did every
`jm remove` of a member (method, property, warning, error -- they share
`_remove._regenerate_object_bindings`), discarding whatever the author had
written there. One file, two owners, depending on the verb. A module
object's bench was never touched by either.

Decided on the issue: the bench is the author's. Neither verb writes it
now, and a bench left calling a removed method is the author's to update --
which `jm remove method` says, beside its "delete it by hand" note, because
deleting the body is exactly what makes such a bench stop linking.

The one render that still sees every declared member is the bench `apply`
materialises when the file is missing: its replay runs `jm method` in a
scratch tree, where the bench is jm's own scaffold (gh-137's reasoning),
so a materialised bench goes on timing the methods the manifest declares.

GATE: for every object-member kind `jm remove` dispatches (classified
      against `_cli_remove._KINDS`, cases shared with gh-1978), an author's
      edit to the C benchmark survives the add and the remove byte for
      byte, on a standalone and a module object, and `status --check` and
      `apply` agree it is the author's; a bench `apply` materialises still
      times the declared methods; and a remove whose bench still calls the
      method says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jm_bench import materialize_bench
from _jmrun import run_cli
from just_makeit import _cli_remove
from just_makeit import _csym as CSYM
from just_makeit import _textio
from test_gh1978_remove_leaves_tree_in_sync import CASES

#: The `jm remove` kinds whose add and remove change an OBJECT's members,
#: and so must leave that object's benchmark alone.
MEMBER_KINDS = ("method", "property", "warning", "error")

#: The rest, and why the gate above does not apply to each. Together with
#: `MEMBER_KINDS` this is exactly `_cli_remove._KINDS`, so a new kind fails
#: `test_every_remove_kind_is_classified` until someone decides.
NOT_MEMBER_KINDS = {
    "object": "removes the object, and its bench with it.",
    "module": "removes the module and its objects.",
    "function": "a module's free function: no object, so no object bench.",
    "state": "`jm add` rebuilds the object, discarding hand-written code,"
    " after asking (gh-1889); `remove state` is the same rebuild.",
    "app": "an app is not a member of an object: removing one re-renders"
    " no object's glue (gh-2074).",
}

#: What the author wrote. Valid C at the end of the file, so the edited
#: bench still builds.
EDIT = "\n/* the author's own notes on this benchmark (gh-1987) */\n"


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout


def _bench(root: Path, obj: str = "o") -> Path:
    return root / "native" / "benchmarks" / f"bench_{obj}_core.c"


def _same(root: Path, want: bytes, after: str) -> None:
    got = _bench(root).read_bytes()
    assert got == want, (
        f"{after} rewrote the author's benchmark; the edit is "
        f"{'kept' if EDIT.encode() in got else 'GONE'}"
    )


def _agree(root: Path, want: bytes, after: str) -> None:
    """`status` does not call the edited bench drift, and `apply` -- the
    other reader of its classification -- does not touch it either."""
    s = run_cli("status", "--check", cwd=root)
    assert s.returncode == 0, f"after {after}:\n{s.stdout}"
    _ok(root, "apply")
    _same(root, want, f"`apply` after {after}")


def test_every_remove_kind_is_classified():
    assert set(MEMBER_KINDS) | set(NOT_MEMBER_KINDS) == set(_cli_remove._KINDS)
    assert not set(MEMBER_KINDS) & set(NOT_MEMBER_KINDS)


@pytest.mark.parametrize(
    "kind, flavor",
    [(k, f) for k in MEMBER_KINDS for f in CASES[k]],
    ids=[f"{k}-{f}" for k in MEMBER_KINDS for f in CASES[k]],
)
def test_the_authors_bench_survives_add_and_remove(tmp_path, kind, flavor):
    setup, add, rest = CASES[kind][flavor]
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    for argv in setup:
        _ok(root, *argv)
    bench = _bench(root)
    assert bench.is_file(), "the case's object has no benchmark to edit"
    _textio.write_text(bench, bench.read_text(encoding="utf-8") + EDIT)
    want = bench.read_bytes()

    for argv in add:
        _ok(root, *argv)
    _same(root, want, f"`jm {' '.join(add[0][:2])}`")
    _agree(root, want, "the add")

    _ok(root, "remove", kind, *rest, "--force")
    _same(root, want, f"`jm remove {kind}`")
    _agree(root, want, "the remove")


def _times(root: Path, sym: str) -> bool:
    """Whether the bench's CODE calls *sym* -- not a comment naming it."""
    text = _bench(root).read_text(encoding="utf-8")
    return bool(CSYM.references(text, {sym}))


def test_a_materialised_bench_still_times_the_methods(tmp_path):
    """The replay's exception: with no bench on disk, `apply` writes the one
    jm renders for the whole manifest, methods included -- as it did before
    the verbs stopped writing it."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "object", "o")
    _ok(root, "method", "o", "m")
    # The verb itself no longer adds the timing block...
    assert not _times(root, "p_o_m")
    materialize_bench(root, "o")
    # ...and a bench materialised from the manifest has it.
    assert _times(root, "p_o_m"), _bench(root).read_text(encoding="utf-8")


def test_the_remove_says_the_bench_still_calls_it(tmp_path):
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "object", "o")
    _ok(root, "method", "o", "m")
    materialize_bench(root, "o")
    want = _bench(root).read_bytes()
    assert _times(root, "p_o_m")

    out = _ok(root, "remove", "method", "m", "--object", "o", "--force")

    _same(root, want, "`jm remove method`")
    assert (
        "note: native/benchmarks/bench_o_core.c still calls p_o_m()" in out
    ), out


@pytest.mark.parametrize(
    "shape",
    [
        # The CLI path: `jm method` never put a timing block in.
        (),
        # A no-step object whose method jm cannot time: the bench NAMES it,
        # in the TODO comment listing the candidates, and calls nothing.
        ("--no-step", "--variable-output"),
    ],
    ids=["never-timed", "named-in-a-comment"],
)
def test_no_note_when_the_bench_does_not_call_it(tmp_path, shape):
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    no_step = "--no-step" in shape
    _ok(root, "object", "o", *(("--no-step",) if no_step else ()))
    _ok(root, "method", "o", "m", *[a for a in shape if a != "--no-step"])
    if no_step:
        materialize_bench(root, "o")
        text = _bench(root).read_text(encoding="utf-8")
        assert "p_o_m(obj, ...)" in text, text  # the case is what it says
    assert not _times(root, "p_o_m")

    out = _ok(root, "remove", "method", "m", "--object", "o", "--force")

    assert "still calls" not in out, out


def test_no_note_when_the_author_deleted_the_bench(tmp_path):
    """The file is theirs to delete as well as to edit."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "object", "o")
    _ok(root, "method", "o", "m")
    _bench(root).unlink()

    out = _ok(root, "remove", "method", "m", "--object", "o", "--force")

    assert "still calls" not in out, out
    assert not _bench(root).exists()
