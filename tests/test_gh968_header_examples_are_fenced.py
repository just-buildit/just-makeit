"""gh-968: a code example in a shipped header's doxygen block must be fenced.

`jm_simd.h`'s file comment showed its FIR usage example unfenced. Doxygen then
treats it as prose, mkdoxy emits it into markdown as prose, and CommonMark
reads the `[k]` in `coeffs[k]` as a **shortcut link reference** with no
matching definition — which fails a strict markdown build downstream.

`@code` / `@endcode` is the fix and it costs two lines. The gate is here
because the next example someone adds to a shipped header will be written the
same way, and the failure surfaces two repos away in a docs build.

**It checks the symptom, not "does this look like code".** The first version
matched indented lines ending in a statement character, and immediately
false-positived on the instruction-set table at the top of this same file —
prose that breaks nothing. A gate that fires on things that cannot break is one
people learn to silence, so what is asserted here is the construct CommonMark
actually mangles. An unfenced example containing none of it is not a defect.

**Scope, measured rather than assumed.** Sweeping every header found indented
example blocks in three files, and only ONE of them reaches a renderer:

    jm_simd.h    /** file block, contains the example    -> doxygen sees it
    jm_perf.h    /** block is a 6-line blurb; the JM_DEFINE_STEPS text
                 below it is a plain /* comment          -> invisible
    jm_test.h    no /** block anywhere                   -> invisible
    jm_bench.h   no /** block anywhere                   -> invisible

`EXTRACT_ALL` does not change that: it extracts undocumented *entities*, not
plain `/*` comments. So this checks doxygen blocks specifically.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INC = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "templates"
    / "c"
    / "inc"
)

# CommonMark reads `word[token]` outside a fence as a shortcut link reference.
_LINKREF = re.compile(r"\[[A-Za-z_][A-Za-z0-9_]*\]")


def _doxygen_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Every `/** ... */` block as (starting line number, lines).

    Only `/**`. A plain `/*` comment is not documentation and never reaches
    the markdown, so including it would invent findings.
    """
    blocks, cur, start = [], None, 0
    for i, line in enumerate(text.splitlines(), 1):
        if cur is None and line.lstrip().startswith("/**"):
            cur, start = [line], i
        elif cur is not None:
            cur.append(line)
            if "*/" in line:
                blocks.append((start, cur))
                cur = None
    return blocks


HEADERS = sorted(INC.glob("*.h"))


def test_the_sweep_actually_sees_the_headers():
    """Guard the guard: an empty corpus would make every check below vacuous."""
    assert len(HEADERS) >= 5, [p.name for p in HEADERS]


@pytest.mark.parametrize("header", HEADERS, ids=lambda p: p.name)
def test_no_bare_link_reference_in_a_doxygen_block(header):
    """The specific breakage gh-968 reported, checked directly.

    `coeffs[k]` outside a fence is a shortcut link reference with no
    definition. Asserting the *symptom* as well as the cause means a future
    example that avoids `@code` but still trips CommonMark is caught too.
    """
    text = header.read_text(encoding="utf-8")
    findings = []
    for start, lines in _doxygen_blocks(text):
        fenced = False
        for line in lines:
            low = line.lower()
            if "@code" in low:
                fenced = True
                continue
            if "@endcode" in low:
                fenced = False
                continue
            if not fenced and _LINKREF.search(line):
                findings.append(f"{header.name}:~{start}: {line.strip()}")
    assert not findings, (
        "`[token]` outside a fence in a doxygen block — CommonMark reads it "
        "as a link reference with no definition:\n  " + "\n  ".join(findings)
    )
