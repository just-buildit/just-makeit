"""_textio.py -- the one way jm writes a text file (gh-1368).

``Path.write_text`` in text mode translates ``\\n`` to the platform's line
ending, so on Windows every file jm generated came out CRLF: the same manifest
produced different bytes on different machines, and the first clang-cl run
failed cmake-lint's ``C0327 Wrong line ending (windows)`` on the generated
``CMakeLists.txt``. Python 3.9 (still supported) has no ``newline=`` on
``write_text``, hence a function rather than a keyword at each call site.

``tests/test_gh1368_lf_writes.py`` refuses any other ``.write_text(`` in the
package, so a new call site cannot reintroduce it.

``utf8_stdio`` is the same rule for what jm *prints* (gh-1387).
"""

from __future__ import annotations

import sys
from pathlib import Path


#: gh-2095: how many TOML files jm has written in this process -- see
#: :func:`toml_writes`.
_TOML_WRITES = 0


def toml_writes() -> int:
    """How many TOML files jm has written in this process (gh-2095).

    Every :func:`write_text` of a ``.toml`` path counts, and so does each
    write :func:`wrote_toml` reports. A cache of the merged manifest records
    this when it fills and is stale once it has moved: everything
    `C.load` reads is TOML -- the manifest, its fragments,
    ``pyproject.toml`` -- and within one process what changes one is jm
    writing it. So `_incpath.manifest` keys on it, at one integer compare
    a lookup rather than a ``stat`` of every fragment.

    Only TOML, because a count of EVERY write was measured useless: the
    generated C and stubs written between lookups forced 107 reloads in
    one doppler `jm status` (4.98 s in the cache, against 0.69 s keyed on
    the manifest alone). Counting TOML: 6 reloads, 0.89 s (2026-10-09).

    Examples
    --------
    >>> import tempfile
    >>> d, before = Path(tempfile.mkdtemp()), toml_writes()
    >>> _ = write_text(d / "x.c", "x")
    >>> toml_writes() == before
    True
    >>> _ = write_text(d / "x.toml", "x = 1")
    >>> toml_writes() == before + 1
    True
    """
    return _TOML_WRITES


def wrote_toml() -> None:
    """Count a TOML write jm made without :func:`write_text` (gh-2095).

    For what a text write cannot express -- `C.save` removing an emptied
    fragment, `apply` copying one in -- so :func:`toml_writes` sees them.

    Examples
    --------
    >>> before = toml_writes()
    >>> wrote_toml()
    >>> toml_writes() == before + 1
    True
    """
    global _TOML_WRITES
    _TOML_WRITES += 1


def write_text(path: Path, text: str) -> int:
    r"""Write *text* to *path* as UTF-8 with ``\n`` line endings, anywhere.

    Returns the number of characters written, as ``Path.write_text`` does.
    A ``.toml`` *path* is counted by :func:`toml_writes` (gh-2095).

    Examples
    --------
    >>> import tempfile
    >>> p = Path(tempfile.mkdtemp()) / "x.txt"
    >>> write_text(p, "a\nb\n")
    4
    >>> p.read_bytes()
    b'a\nb\n'
    """
    # Counted before the write, so one that fails half-way still counts.
    if Path(path).suffix == ".toml":
        wrote_toml()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        return fh.write(text)


def lf(data: bytes) -> bytes:
    r"""*data* with every CRLF line ending spelled LF, as jm writes it.

    The one normalisation behind :func:`eol_only`, and what `status` diffs
    when it shows a file whose real change sits under a CRLF checkout -- a
    diff of the raw bytes reports every line. A lone ``\r`` is content, not
    a line ending, and is left alone.

    Examples
    --------
    >>> lf(b"a\r\nb\r\n")
    b'a\nb\n'
    >>> lf(b"a\rb\n")
    b'a\rb\n'
    """
    return data.replace(b"\r\n", b"\n")


def eol_only(disk: bytes, render: bytes) -> bool:
    r"""True when *disk* differs from *render* only in CRLF versus LF.

    gh-1641: jm writes LF everywhere (:func:`write_text`), and Git for
    Windows checks text files out CRLF unless the project says otherwise, so
    a fresh Windows clone differed from jm's render in every line of every
    file. No compiler, CMake, Python or clang-format jm drives reads the two
    differently, so that difference is not drift. This is the ONE predicate
    for it: `status` reports such a file in its own uncounted row instead of
    STALE or OUTDATED, and `apply` reports the rewrite to LF as ``eol``
    rather than ``update``. Two copies of the rule are the peer pair that
    drifts -- one would say STALE about a file the other calls current.

    False for identical bytes: an equal file has no difference to classify.

    Examples
    --------
    >>> eol_only(b"a\r\nb\r\n", b"a\nb\n")
    True
    >>> eol_only(b"a\nb\n", b"a\nb\n")
    False
    >>> eol_only(b"a\r\nc\r\n", b"a\nb\n")
    False
    """
    return disk != render and lf(disk) == lf(render)


def utf8_stdio() -> None:
    r"""Make ``sys.stdout`` and ``sys.stderr`` encode UTF-8, anywhere.

    jm's messages carry characters such as ``\u2192`` and ``\u2014``. A
    Windows console is already UTF-8, but a *pipe* or a redirect is not:
    Python encodes it in the ANSI code page (cp1252 on an English system),
    which has no ``\u2192``, so ``jm upgrade > log.txt`` -- or any CI log --
    died with ``UnicodeEncodeError`` part-way through a migration. The
    0.77.1 release smoke found it on its first Windows run (gh-1387).

    Fixed for the stream rather than for each message: there are dozens,
    and a new one would reintroduce it. A stream that is not a
    ``TextIOWrapper`` (pytest's capture, ``None`` under ``pythonw``) has
    no ``reconfigure`` and is left alone.

    ``tests/test_gh1387_utf8_stdio.py`` runs ``main()`` under
    ``PYTHONIOENCODING=cp1252`` and requires this to hold.

    Examples
    --------
    >>> utf8_stdio()  # a no-op where the stream is already UTF-8
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if (stream.encoding or "").lower().replace("-", "") != "utf8":
            reconfigure(encoding="utf-8")
