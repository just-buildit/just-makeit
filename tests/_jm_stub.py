"""Test helper: take jm's placeholder for an accessor out of a C source.

gh-1303: `jm property` now scaffolds a marked no-op body for every accessor
it declares, the way `jm method` always has. A fixture that supplies its own
full definition afterwards has to drop the placeholder first, exactly as an
author replaces it -- otherwise the component defines the symbol twice.
"""

from __future__ import annotations

import re


def drop_jm_stubs(text: str, *names: str) -> str:
    """*text* without jm's ``<<IMPLEMENT: name>>`` stub for each of *names*.

    Each stub must be present exactly once: a fixture that asks to drop one
    jm did not write is describing a tree it does not have.
    """
    for name in names:
        pat = re.compile(
            rf"/\* <<IMPLEMENT: {re.escape(name)}>> \*/\n.*?\n\}}\n", re.S
        )
        assert len(pat.findall(text)) == 1, f"no single jm stub for {name}"
        text = pat.sub("", text)
    return text
