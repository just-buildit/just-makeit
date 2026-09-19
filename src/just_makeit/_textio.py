"""_textio.py -- the one way jm writes a text file (gh-1368).

``Path.write_text`` in text mode translates ``\\n`` to the platform's line
ending, so on Windows every file jm generated came out CRLF: the same manifest
produced different bytes on different machines, and the first clang-cl run
failed cmake-lint's ``C0327 Wrong line ending (windows)`` on the generated
``CMakeLists.txt``. Python 3.9 (still supported) has no ``newline=`` on
``write_text``, hence a function rather than a keyword at each call site.

``tests/test_gh1368_lf_writes.py`` refuses any other ``.write_text(`` in the
package, so a new call site cannot reintroduce it.
"""

from __future__ import annotations

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
