"""gh-2269: no integer type parses through a platform-width format char.

``l`` and ``k`` parse into a C ``long``, which is 64 bits on LP64 and 32
on Windows (LLP64). ``int32_t`` parsed through ``l``, so on Windows a value
past ``int32_t`` was refused by CPython's own "Python int too large to
convert to C long" before jm's range check (gh-2144) could say "out of
range for int32_t [lo, hi]": one message per platform. Every row of the
type registry is checked, so a type added later is covered unasked.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _types as T  # noqa: E402

#: PyArg format chars whose C type is `long` / `unsigned long`.
PLATFORM_WIDTH = {"l", "k"}


def test_no_integer_row_parses_through_a_c_long():
    bad = {
        name: meta["fmt"]
        for name, meta in T._CTYPE_META.items()
        if meta.get("kind") == "int" and meta.get("fmt") in PLATFORM_WIDTH
    }
    assert not bad, bad


def test_int32_parses_like_uint32():
    """The two 32-bit rows share the one checked, platform-fixed parse."""
    i32, u32 = T._CTYPE_META["int32_t"], T._CTYPE_META["uint32_t"]
    assert i32["fmt"] == u32["fmt"] == "L"
    assert i32.get("bounds"), "int32_t must still be range-checked"
