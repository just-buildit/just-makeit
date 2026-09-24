"""`_code_mask` keeps its contract, now that it is one regex pass (gh-1374).

It was a per-character loop and the largest single cost in the suite, ~20%
of a serial session's CPU on every interpreter. The rewrite was checked
against the loop by a differential fuzz (200,000 random strings over the
characters that matter, and every C/H/CMake file in the repo): identical,
except that the loop broke its own "same length" promise 7,899 times -- a
lone backslash ending an open literal emitted two spaces for one character.

These are the properties any correct mask has, so they hold without keeping
a second copy of the old loop around as an oracle:

- the mask is exactly as long as the text, so an offset found in one is the
  same offset in the other (every caller slices the original with it);
- whatever survives unblanked is the original character at that position;
- a newline never appears where the text has none;
- the edge cases the grammar exists for.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docsync import _code_mask  # noqa: E402

_ALPHABET = "\"'\\/*\n ab{};"


def _samples(n: int = 20000) -> "list[str]":
    rng = random.Random(1374)
    return [
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 24)))
        for _ in range(n)
    ]


def test_the_mask_is_exactly_as_long_as_the_text():
    bad = [s for s in _samples() if len(_code_mask(s)) != len(s)]
    assert not bad, f"{len(bad)} inputs, e.g. {bad[0]!r}"


def test_what_survives_is_the_original_character():
    for s in _samples():
        m = _code_mask(s)
        for i, ch in enumerate(m):
            if ch not in " \n":
                assert ch == s[i], (s, m, i)
            if ch == "\n":
                assert s[i] == "\n", (s, m, i)


@pytest.mark.parametrize(
    "text, mask",
    [
        # comment markers inside a literal are not comments
        ('x("//", 1);', 'x("  ", 1);'),
        ('x("/*", 1);', 'x("  ", 1);'),
        # an escaped quote does not close the literal
        ('a"b\\"c"d', 'a"    "d'),
        ("a'\\''b", "a'  'b"),
        # a block comment keeps its newlines and does not close on /*/
        ("/*/ x */y", "        y"),
        ("a/*\n*/b", "a  \n  b"),
        # a line comment ends at the newline, which is kept
        ("a // b\nc", "a     \nc"),
        # open to the end of the text
        ('a"bc', 'a"  '),
        ("a/* bc", "a     "),
        # the old loop's one defect: a lone trailing backslash in a literal
        ('"\\', '" '),
        ("'ab\\", "'   "),
    ],
)
def test_the_edge_cases(text, mask):
    assert _code_mask(text) == mask
    assert len(_code_mask(text)) == len(text)
