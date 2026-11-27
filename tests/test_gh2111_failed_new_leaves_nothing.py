"""gh-2111: a `jm new` that fails leaves no project behind.

`jm new` wrote the project and only then scaffolded the objects and
modules it was given, so a refusal in that second half left a half-made
project and exited 1: `jm new p --object o --object o` left 38 files,
`--module m --module m` 31 (v0.138.3). The next `jm new p` then refused
the non-empty directory they were in.

`_undo` puts a MUTATING command's project back when it fails (gh-1867,
gh-2040); `new` is CREATES, its root unknown until its argv is read, so
the guard `_cli.main` opens for it waits for `_new.run` to name the root
(`_undo.creating`). A root that did not exist goes, with any parent the
command made on the way to it; one that existed empty gets the per-file
record every root does, and stays -- a directory that existed is never
removed.

GATE: each way out but success -- the issue's two refusals, a refusal, an
      exception and an exit injected partway through the writes, and the
      same three where the command would have succeeded -- leaves the
      working directory as it was, for each kind of root. A success keeps
      the project. `test_gh2057_verb_leaves_what_apply_writes.py` holds
      every `NEW_CASES` flag set to the same.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli


def _tree(base: Path) -> "dict[str, bytes]":
    """Every file and directory under *base*; a directory reads as a
    marker, so one left behind, or one gone, is a change too."""
    return {
        p.relative_to(base).as_posix(): (
            p.read_bytes() if p.is_file() else b"<dir>"
        )
        for p in sorted(base.rglob("*"))
    }


#: Where the project goes, as (argv after the name, working directory
#: relative to the base, a directory that exists empty beforehand).
ROOTS = {
    "absent": ((), ".", None),
    "nested-absent": (("x/y/p",), ".", None),
    "empty": ((), ".", "p"),
    "cwd-empty": ((".",), "here", "here"),
}


@pytest.fixture(params=sorted(ROOTS))
def where(request, tmp_path):
    """A base with the author's own file in it, the root's argv, the
    working directory, and the base's tree before the run."""
    extra, cwd, empty = ROOTS[request.param]
    (tmp_path / "mine.txt").write_bytes(b"the author's\n")
    if empty is not None:
        (tmp_path / empty).mkdir()
    return tmp_path, extra, tmp_path / cwd, _tree(tmp_path)


#: What a guard that put files back prints, whichever record it held.
_SAYS_SO = "this command had"


def _left_as_it_was(where, *flags: str):
    """`jm new p <root> <flags>` exits 1 and leaves the base as it was."""
    base, extra, cwd, before = where
    r = run_cli("new", "p", *extra, *flags, cwd=cwd)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-3000:]
    assert _tree(base) == before, out[-3000:]
    return r


@pytest.mark.parametrize(
    "flags, says",
    [
        (("--object", "o", "--object", "o"), "component 'o' already"),
        (("--module", "m", "--module", "m"), "module 'm' already"),
    ],
    ids=["object-twice", "module-twice"],
)
def test_the_issue_refusals_leave_nothing(where, flags, says):
    r = _left_as_it_was(where, *flags)
    assert says in r.stderr, r.stderr
    assert _SAYS_SO in r.stdout, "it wrote nothing before refusing"


def _raiser(kind: str):
    from just_makeit._report import Refusal

    def fail(*_a, **_k):
        if kind == "refusal":
            raise Refusal("injected: refused")
        if kind == "exit":
            raise SystemExit(1)
        raise RuntimeError("injected: crashed")

    return fail


KINDS = ["refusal", "exception", "exit"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_failure_partway_leaves_nothing(where, monkeypatch, kind):
    """The tenth text file jm writes fails: the root, its parents and the
    files before it are the command's, and go."""
    from just_makeit import _textio

    real, calls = _textio.write_text, []
    fail = _raiser(kind)

    def write_text(*a, **k):
        calls.append(a)
        if len(calls) == 10:
            fail()
        return real(*a, **k)

    monkeypatch.setattr(_textio, "write_text", write_text)
    _left_as_it_was(where, "--object", "o")
    assert len(calls) == 10, "the injected failure never fired"


@pytest.mark.parametrize("kind", KINDS)
def test_a_failure_at_success_leaves_nothing(where, monkeypatch, kind):
    """Where the command would have succeeded (`_undo.commit`), after its
    last write."""
    from just_makeit import _undo

    monkeypatch.setattr(_undo, "commit", _raiser(kind))
    r = _left_as_it_was(where, "--object", "o", "--module", "m")
    assert _SAYS_SO in r.stdout, r.stdout[-3000:]


def test_a_success_keeps_the_project(where):
    base, extra, cwd, before = where
    r = run_cli("new", "p", *extra, "--object", "o", cwd=cwd)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert _SAYS_SO not in r.stdout
    after = _tree(base)
    assert before.items() <= after.items()
    assert any(k.endswith("just-makeit.toml") for k in after), sorted(after)
