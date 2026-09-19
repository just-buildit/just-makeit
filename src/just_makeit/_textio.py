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


def write_text(path: Path, text: str) -> int:
    r"""Write *text* to *path* as UTF-8 with ``\n`` line endings, anywhere.

    Returns the number of characters written, as ``Path.write_text`` does.

    Examples
    --------
    >>> import tempfile
    >>> p = Path(tempfile.mkdtemp()) / "x.txt"
    >>> write_text(p, "a\nb\n")
    4
    >>> p.read_bytes()
    b'a\nb\n'
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        return fh.write(text)


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
