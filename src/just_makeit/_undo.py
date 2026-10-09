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
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Iterator, Optional


class Undo:
    """The project tree at *root* as it was, and how to put it back.

    Parameters
    ----------
    root : Path
        The project root. Every file :func:`_apply.walk` reaches under it
        is read, and every directory it yields recorded, on construction.

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
    """

    def __init__(self, root: Path) -> None:
        from ._apply import walk

        self._root = root
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


#: The record the running command keeps, while :func:`guard` holds one.
_active: "Optional[Undo]" = None


@contextlib.contextmanager
def guard(root: "Optional[Path]") -> Iterator[None]:
    """Run the block as one change to the project at *root*: kept when the
    block calls :func:`commit`, put back on any other way out.

    ``None`` guards nothing -- a command that does not change the project
    in the working directory, or a directory that holds no project.

    When putting it back changed anything, one line on stdout says so,
    under the ``update`` lines it takes back: the refusal may name a file
    the command had deleted, which is back. Not on stderr, whose last line
    stays the refusal for whatever reads it.
    """
    global _active
    if root is None:
        yield
        return
    _active = undo = Undo(root)
    try:
        yield
    finally:
        _active = None
        restored = undo.rollback()
        if restored:
            print(
                f"just-makeit: put back the {restored} file(s) this command"
                " had changed; the project is as it was before it ran."
            )


def commit() -> None:
    """The running command has succeeded: keep what it wrote.

    Called by `_cli.main` when a command returns, and by a command whose
    exit status reports on a write it finished (`jm adopt`) before it
    exits. Outside :func:`guard` it does nothing.
    """
    if _active is not None:
        _active.commit()
