"""The directory a test's project writes its own headers into (gh-1583).

From manifest schema 8 a project's headers live under ``native/inc/<pkg>/``,
not ``native/inc/``. A test that spells ``root / "native" / "inc" / "comp"``
by hand encodes one layout; it spells ``root / INC_ROOT / "comp"`` instead,
and the answer comes from `_incpath.header_root` -- the one owner of the
layout -- reading that project's own manifest.

``INC_ROOT`` works on the right of ``/`` because a ``Path`` hands an operand
it cannot join back to it (``Path.__truediv__`` returns ``NotImplemented``
on a ``TypeError``), and ``__rtruediv__`` then receives the path on the left.
It must be used after the project's manifest exists: before that there is no
schema to read, and ``_incpath`` answers with the legacy layout.

>>> from pathlib import Path
>>> Path("/nowhere") / INC_ROOT
PosixPath('/nowhere/native/inc')
"""

from __future__ import annotations

from pathlib import Path

from just_makeit import _incpath


class _IncRoot:
    def __rtruediv__(self, root: "Path | str") -> Path:
        return _incpath.header_root(Path(root))

    def __repr__(self) -> str:
        return "INC_ROOT"


INC_ROOT = _IncRoot()

#: The ``-I`` directory, which is NOT ``INC_ROOT``: it is ``native/inc`` in
#: every layout, and a prefixed project's includes are spelled ``<pkg>/...``
#: against it. A compile line's ``-I`` uses this.
INC_DIR = _incpath.INC_DIR
