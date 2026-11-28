"""_undo.py -- a mutating command that fails leaves the project as it was.

A verb wrote into the tree before it could fail, so a later failure left a
half-made change (jm#1543, the data-loss tier). `jm regenerate` deleted
the component and only then re-applied it, so a refusal in the re-apply --
an ``impl_file`` inside the deleted files, or another component's broken
row -- lost the component, hand-written ``_core.c`` and all (gh-1867).
`jm method` and `jm property` saved ``_core.c`` and the manifest and only
then rendered the binding, whose refusal left both changed and the binding
not (gh-2040).

`apply` had met the same shape (gh-1660): composing a fragment has to come
before every refusal, because each one reads the manifest WITH it, so it
recorded the files composing would write and put them back on a refusal.
That record is this one, widened from the files one write site names to
the project's whole tree -- the only set that covers a write nobody
listed -- and taken once, by `_cli.main`, around every command
`_cli.COMMANDS` classifies MUTATING. A verb added tomorrow is covered the
day it is classified, with nothing to register.

What a command wrote is kept only when it says it succeeded
(:func:`commit`): `_cli.main` says so when the command returns, and a
command whose exit status reports on a finished write -- `jm adopt`,
where a target that did not flip leaves the ones that did -- says so
before it exits. Every other way out (a refusal, ``sys.exit(1)``, a crash,
an interrupt) puts the tree back byte for byte.

The record is the bytes of every file :func:`_apply.walk` reaches -- the
walk `apply` already reads in full to report what it changed -- held for
the length of one command. Measured 2026-10-08 on doppler, a large jm
project: 3649 files, 76 MB, read in about 60 ms from a warm cache.

A command that CREATES its project (`jm new`) has no tree to record: its
root is a directory that did not exist, or was empty, before it ran, and
where that is only the command knows, once it has read its argv. So
`_cli.main` opens the guard with no root, and the command names it through
:func:`creating` before it writes there. A root that did not exist is
simpler to put back than a snapshot: everything under it is the command's,
so it is removed, with any parent the command made on the way to it. A
directory that existed is never removed; an empty one is recorded like any
other root, so the files the command wrote into it go and it stays
(gh-2111: a refused `jm new` left 38 files behind, and the next `jm new`
refused the non-empty directory they were in).
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Iterator, Optional


class Undo:
    """The project tree at *root* as it was, and how to put it back.

    Parameters
    ----------
    root : Path
        The project root. Every file :func:`_apply.walk` reaches under it
        is read, and every directory it yields recorded, on construction.
        A root that does not exist yet is one the command creates:
        everything it then holds is the command's, and :meth:`rollback`
        removes it, with every parent made on the way to it (gh-2111).

    Examples
    --------
    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> _ = (root / "kept.c").write_bytes(b"/* mine */\\n")
    >>> undo = Undo(root)
    >>> _ = (root / "kept.c").write_bytes(b"/* jm */\\n")
    >>> (root / "new").mkdir()
    >>> _ = (root / "new" / "made.c").write_bytes(b"")
    >>> undo.rollback()
    2
    >>> (root / "kept.c").read_bytes(), (root / "new").exists()
    (b'/* mine */\\n', False)

    A committed record puts nothing back:

    >>> undo = Undo(root)
    >>> (root / "kept.c").unlink()
    >>> undo.commit()
    >>> undo.rollback()
    0

    A root that did not exist goes, with the parents made on the way to
    it:

    >>> made = root / "a" / "b" / "p"
    >>> undo = Undo(made)
    >>> made.mkdir(parents=True)
    >>> _ = (made / "x.c").write_bytes(b"")
    >>> undo.rollback(), (root / "a").exists()
    (1, False)
    """

    def __init__(self, root: Path) -> None:
        from ._apply import walk

        self._root = root
        #: When the root is one the command creates, the outermost of it
        #: and its parents that did not exist: every entry under it is the
        #: command's, and it goes whole.
        self._owned: "Optional[Path]" = None
        if not root.exists():
            self._owned = root
            while not self._owned.parent.exists():
                self._owned = self._owned.parent
        self._files: "Optional[dict[Path, bytes]]" = {}
        self._dirs: "set[Path]" = set()
        for base, names in walk(root):
            self._dirs.add(base)
            for name in names:
                self._files[base / name] = (base / name).read_bytes()

    def commit(self) -> None:
        """Keep the tree as it is now: :meth:`rollback` becomes a no-op."""
        self._files = None

    def rollback(self) -> int:
        """Put the tree back as it was; return how many files that changed.

        A file the command created is deleted, one it changed or deleted
        is written back, and then a directory it created is removed once
        it is empty, deepest first, and one it deleted is made again.
        """
        from ._apply import walk

        if self._files is None:
            return 0
        if self._owned is not None:
            return self._remove_owned()
        dirs: "set[Path]" = set()
        files: "set[Path]" = set()
        for base, names in walk(self._root):
            dirs.add(base)
            files.update(base / name for name in names)
        changed = 0
        for path in files - self._files.keys():
            path.unlink()
            changed += 1
        for path, data in self._files.items():
            if path in files and path.read_bytes() == data:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed += 1
        for d in sorted(dirs - self._dirs, key=lambda d: -len(d.parts)):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        for d in self._dirs - dirs:
            d.mkdir(parents=True, exist_ok=True)
        self._files = None
        return changed

    @property
    def root(self) -> Path:
        """The root this record was taken of."""
        return self._root

    @property
    def creates(self) -> bool:
        """True when the root is one the command creates (gh-2111)."""
        return self._owned is not None

    def _remove_owned(self) -> int:
        """Remove the root the command created, and every parent it made
        on the way to it; return how many files the command had written.

        Every entry is the command's, build trees and caches included, so
        nothing is walked selectively.
        """
        owned = self._owned
        assert owned is not None
        self._files = None
        # gh-2828: a link is removed, never followed. Something that put a
        # symlink where the command's directory was leaves `rmtree` refusing
        # it, and would otherwise leave the target's contents to a walk.
        if owned.is_symlink():
            owned.unlink()
            return 0
        if not owned.exists():
            return 0
        if not owned.is_dir():
            owned.unlink()
            return 1
        removed = sum(1 for p in owned.rglob("*") if not p.is_dir())
        shutil.rmtree(owned)
        return removed


#: The record the running command keeps, while :func:`guard` holds one.
_active: "Optional[Undo]" = None

#: True while a guard opened for a command that creates its project waits
#: for :func:`creating` to name the root.
_awaiting_root = False


@contextlib.contextmanager
def guard(root: "Optional[Path]", *, creates: bool = False) -> Iterator[None]:
    """Run the block as one change to the project at *root*: kept when the
    block calls :func:`commit`, put back on any other way out.

    ``None`` guards nothing -- a command that does not change the project
    in the working directory, or a directory that holds no project --
    unless *creates*: the command creates its project, at a root it names
    through :func:`creating` once its argv says where (gh-2111).

    When putting it back changed anything, one line on stdout says so,
    under the ``update`` lines it takes back: the refusal may name a file
    the command had deleted, which is back. Not on stderr, whose last line
    stays the refusal for whatever reads it.

    A put-back that itself fails (``OSError``: a link or permission change
    under the tree, gh-2828) says which path it left, on one more line, and
    does not raise: the command's own refusal or crash is the error the user
    sees.
    """
    global _active, _awaiting_root
    if root is None and not creates:
        yield
        return
    _active = None if root is None else Undo(root)
    _awaiting_root = root is None
    try:
        yield
    finally:
        undo, _active, _awaiting_root = _active, None, False
        try:
            restored = undo.rollback() if undo is not None else 0
        except OSError as exc:
            # gh-2828: this runs on the way out of a command that failed, so
            # an error raised here would replace the one the user needs. Say
            # what is left, on a line of its own, and let the command's own
            # error leave.
            assert undo is not None
            left = Path(exc.filename) if exc.filename else undo.root
            print(
                f"just-makeit: could not {'remove' if undo.creates else 'put back'}"
                f" {left}: {exc.strerror or exc}; it is left behind."
            )
            restored = 0
        if restored and undo is not None and undo.creates:
            print(
                f"just-makeit: removed {undo.root} and the {restored}"
                " file(s) this command had written there; it did not exist"
                " before it ran."
            )
        elif restored:
            print(
                f"just-makeit: put back the {restored} file(s) this command"
                " had changed; the project is as it was before it ran."
            )


def creating(root: Path) -> None:
    """The running command is about to write its new project at *root*.

    Called by `_new.run` once it has refused a root that is not empty and
    before it writes there. Under the guard `_cli.main` opens for a command
    that creates its project, *root* is recorded: a root that did not exist
    is removed on any way out but :func:`commit`, and one that existed,
    empty, gets the per-file record every root does (gh-2111). Anywhere else -- `apply`'s replay
    into a scratch directory, a caller of the Python API -- it does
    nothing: the guard there, if any, records the project it changes.
    """
    global _active, _awaiting_root
    if _awaiting_root:
        _active, _awaiting_root = Undo(root), False


def commit() -> None:
    """The running command has succeeded: keep what it wrote.

    Called by `_cli.main` when a command returns, and by a command whose
    exit status reports on a write it finished (`jm adopt`) before it
    exits. Outside :func:`guard` it does nothing.
    """
    if _active is not None:
        _active.commit()
