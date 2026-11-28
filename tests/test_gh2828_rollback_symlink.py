"""gh-2828: a rollback that cannot finish does not mask the command's error.

A failed `jm new` removes the root it created through ``_undo``'s CREATES
guard (gh-2111). If a symlink to a directory appears at the missing-parent
position -- the outermost directory the command made, the one the guard
removes whole -- between the command's start and the rollback,
``shutil.rmtree`` raised ``OSError: Cannot call rmtree on a symbolic link``
inside the guard's ``finally``, and that replaced the command's own error: the
user got an ``OSError`` traceback in place of the refusal that stopped
``jm new``.

GATE: with that symlink in place, the refusal the command raised is what
exits 1 and what stderr ends on, with no traceback; the link is removed, never
followed, so the directory it points at keeps everything it held; and a
cleanup that fails for any other reason says, on a line of its own, which path
it left behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _jmrun import run_cli

from just_makeit import _undo


def _swap_in_a_symlink(base: Path):
    """The racing writer: replace ``x`` (the directory `jm new` made) with a
    symlink to a directory of the author's own, holding a file."""
    target = base / "theirs"
    target.mkdir()
    (target / "keep.txt").write_bytes(b"the author's\n")
    mine = base / "x"
    for p in sorted(mine.rglob("*"), key=lambda p: -len(p.parts)):
        p.rmdir() if p.is_dir() else p.unlink()
    mine.rmdir()
    mine.symlink_to(target, target_is_directory=True)
    return target


def _fail_at_the_tenth_write(monkeypatch, base: Path, during):
    from just_makeit import _textio
    from just_makeit._report import Refusal

    real, calls = _textio.write_text, []

    def write_text(*a, **k):
        calls.append(a)
        if len(calls) == 10:
            during(base)
            raise Refusal("injected: refused")
        return real(*a, **k)

    monkeypatch.setattr(_textio, "write_text", write_text)
    return calls


def test_a_symlink_at_the_parent_does_not_mask_the_refusal(
    tmp_path, monkeypatch
) -> None:
    calls = _fail_at_the_tenth_write(monkeypatch, tmp_path, _swap_in_a_symlink)
    r = run_cli("new", "p", "x/y/p", "--object", "o", cwd=tmp_path)
    out = r.stdout + r.stderr
    assert len(calls) == 10, "the injected failure never fired"
    assert r.returncode == 1, out[-3000:]
    assert "Traceback" not in out and "OSError" not in out, out[-3000:]
    assert "injected: refused" in r.stderr, r.stderr
    assert r.stderr.rstrip().splitlines()[-1].endswith("injected: refused")
    # The link goes, and what it pointed at is untouched.
    assert not (tmp_path / "x").exists() and not (tmp_path / "x").is_symlink()
    assert (tmp_path / "theirs" / "keep.txt").read_bytes() == b"the author's\n"


def test_a_cleanup_that_fails_names_what_it_left(
    tmp_path, monkeypatch, capsys
) -> None:
    """Any other ``OSError`` from the cleanup is a second line, not the
    error the caller sees."""
    import shutil

    def no(*_a, **_k):
        raise PermissionError(13, "Permission denied", str(tmp_path / "x"))

    monkeypatch.setattr(shutil, "rmtree", no)
    root = tmp_path / "x" / "p"
    with pytest.raises(RuntimeError, match="the real error"):
        with _undo.guard(None, creates=True):
            _undo.creating(root)
            root.mkdir(parents=True)
            raise RuntimeError("the real error")
    out = capsys.readouterr().out
    assert f"could not remove {tmp_path / 'x'}" in out, out
    assert "left behind" in out, out
    assert (tmp_path / "x").is_dir(), "the fake failure removed it"
