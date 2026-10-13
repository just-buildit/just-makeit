"""gh-2258: an ``index`` row compares its maximum before the cast.

gh-2220 parses a ``uint64_t`` / ``size_t`` as an object and converts it
through ``PyNumber_Index`` and the checked ``PyLong_AsUnsignedLongLong``
(`_context._parse._index_c`). That converter checks against its own return
type, ``unsigned long long``. Where ``size_t`` is 32 bits, a value between
``SIZE_MAX`` and ``2**64`` passed it and then wrapped on the cast to
``size_t``: the row's maximum was printed in the refusal and never
compared.

No CI target has a 32-bit ``size_t``, so the gate is at the render: every
row carrying ``index`` -- read off ``_CTYPE_META``, not listed here -- is
rendered through both public slots that convert one (`scalar_parse_c`, a
setter's or composer row's; `scalar_arg_c`, every tuple parse's), and the
refusal's condition must compare the converted value with the maximum the
row's ``limits`` names, before the line that casts it.

GATE: every ``index`` integer row's converter compares the value with the
    row's ``limits`` maximum before the cast, on both conversion slots.
"""

from __future__ import annotations

import re

import pytest

from just_makeit._context._parse import scalar_arg_c, scalar_parse_c
from just_makeit._types import _CTYPE_META

INDEX = sorted(t for t, m in _CTYPE_META.items() if "index" in m)


def test_the_sweep_is_not_vacuous():
    """An empty derivation would check nothing and pass."""
    assert {"uint64_t", "size_t"} <= set(INDEX), INDEX


def _renders(ctype: str) -> "dict[str, str]":
    """The C each slot converts a *ctype* argument ``v`` with."""
    _decl, _fmt, _addr, conv = scalar_arg_c(
        "v", ctype, "", fail="return NULL;"
    )
    return {
        "scalar_parse_c": scalar_parse_c(
            ctype, "o", "v", "return -1;", label="v"
        ),
        "scalar_arg_c": conv,
    }


@pytest.mark.parametrize("ctype", INDEX)
def test_the_maximum_is_compared_before_the_cast(ctype):
    meta = _CTYPE_META[ctype]
    hi = meta["limits"][1]
    pt = meta["parse_type"]
    for slot, c in _renders(ctype).items():
        convert = c.index(f"v_raw = {meta['index']}(_ix);")
        cast = c.index(f"{ctype} v = ({ctype})v_raw;")
        # The refusal's whole condition: from its `if (` to the `{` that
        # opens its body, so a comparison anywhere else does not count.
        m = re.compile(r"if \((.*?)\) \{\n", re.S).search(c, convert)
        assert m and m.end() < cast, (slot, c)
        cond = " ".join(m.group(1).split())
        assert f"v_raw > ({pt}){hi}" in cond, (slot, cond)
        assert "PyExc_OverflowError" in c[m.end() : cast], (slot, c)
