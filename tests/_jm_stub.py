"""Test helper: take jm's placeholder for a member out of a C source.

gh-1303: `jm property` now scaffolds a marked no-op body for every accessor
it declares, the way `jm method` always has. A fixture that supplies its own
full definition afterwards has to drop the placeholder first, exactly as an
author replaces it -- otherwise the component defines the symbol twice.
gh-1978 drops a removed method's body, as `jm remove method`'s note tells
the author to.
"""

from __future__ import annotations

import re


def drop_jm_stubs(text: str, *names: str) -> str:
    """*text* without jm's ``<<IMPLEMENT: name>>`` stub for each of *names*.

    The spellings jm stamps: an accessor's marker names its C symbol,
    ``<<IMPLEMENT: o_get_level>>``; a method's names the method, with a
    space before the close, ``<<IMPLEMENT: m >>``, and a shape note after
    the name for some (gh-2055), ``<<IMPLEMENT: wait (borrowed view) >>``.

    Each stub must be present exactly once: a fixture that asks to drop one
    jm did not write is describing a tree it does not have.

    Examples
    --------
    >>> src = "/* <<IMPLEMENT: wait (borrowed view) >> */\\nT\\nf()\\n{\\n}\\n"
    >>> drop_jm_stubs(src, "wait")
    ''
    """
    for name in names:
        pat = re.compile(
            rf"/\* <<IMPLEMENT: {re.escape(name)}(?: \([^)]*\))? ?>> \*/"
            r"\n.*?\n\}\n",
            re.S,
        )
        assert len(pat.findall(text)) == 1, f"no single jm stub for {name}"
        text = pat.sub("", text)
    return text
