#!/usr/bin/env python3
"""Refuse a changelog code span holding a run of whitespace (gh-1630).

A code span renders its content verbatim, so `` `jm   upgrade` `` reaches
the GitHub Release notes, and CHANGELOG.md on GitHub, as ``jm   upgrade``.
Released sections hold 65 spans with a run (2026-10-01, ``findings`` over
CHANGELOG.md; the issue's line regex counted 81 because it pairs a closing
backtick with the next span's opening one), all but a handful accidents.

**The formatter makes them.** A fragment is a list item whose continuation
lines are indented four spaces. Hand-wrap a code span across a line break,
`` `jm`` / ``    upgrade` ``, and ``make format``'s mdformat (markdown-it)
reads the span's content as ``jm`` + newline + the two spaces of indent
beyond the item's own, turns the newline into a space and writes the span
back on ONE line: `` `jm   upgrade` ``. Measured on mdformat 1.0.0, the
pinned one. After that the run is just text inside a span, and no later
format pass touches it. So this does not look for a span split across lines
-- by the time ``make lint`` runs, the pre-commit hook has already turned
the split into the run -- it looks for the run, which is the defect in both
states: a split span with its indent is a newline-and-spaces run too.

Why a gate and not a repair in the assembler: the assembler
(``scripts/changelog.py``) is vendored verbatim from canonical, and a run
inside a code span is not always an accident (``"    Parameters"`` quoted
from a docstring is one that shipped on purpose), so rewriting span content
silently could change what an entry says. Refusing names the line and lets
the author choose: join the span, or move a deliberately spaced snippet
into a fenced code block, where whitespace is the point and this does not
look.

Scope is what has not shipped: every fragment under ``changelog.d/``, and
the ``[Unreleased]`` section of ``CHANGELOG.md``. A released section is
history (``changelog-sections-check`` refuses any edit to one), so those
runs stay as shipped.

Usage::

    python3 scripts/check_code_spans.py [ROOT]     (or: make code-span-check)

Exit 0 when clean, 1 with one ``path:line: span`` line per finding.

>>> [s for _, s in spans("a `jm\\n    upgrade` b")]
['jm\\n    upgrade']
>>> [s for _, s in spans("``a ` b`` and `c`")]
['a ` b', 'c']
>>> [s for _, s in spans("a lone ` pairs with the next `x`")]
[' pairs with the next ']
>>> [s for _, s in spans("```\\nfenced  code\\n```\\n")]
[]
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Iterator, List, Tuple

FRAGMENT_DIR = "changelog.d"
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"

#: A fenced code block's opening or closing line. Inside one, whitespace is
#: content and backticks are not span delimiters.
_FENCE = re.compile(r"^ *(`{3,}|~{3,})")
#: The defect: two or more whitespace characters in a row. A newline counts,
#: so a span still split across lines (with the item's indent) is caught
#: before the formatter has joined it.
_RUN = re.compile(r"\s{2,}")


def _blank_fences(text: str) -> str:
    """*text* with every fenced block's lines emptied, line count kept."""
    out: List[str] = []
    fence = ""
    for line in text.splitlines(keepends=True):
        m = _FENCE.match(line)
        if fence:
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
            ):
                fence = ""
            out.append("\n")
        elif m:
            fence = m.group(1)
            out.append("\n")
        else:
            out.append(line)
    return "".join(out)


def spans(text: str) -> Iterator[Tuple[int, str]]:
    """(offset, content) of each code span in *text*, CommonMark-paired.

    A run of N backticks opens a span closed by the next run of exactly N;
    a run with no partner is literal text and pairing resumes after it. A
    backslash escapes one backtick outside a span, and nothing inside one.
    Fenced blocks are skipped.

    >>> [s for _, s in spans(r"a \\`b `c\\` d")]
    ['c\\\\']
    """
    text = _blank_fences(text)
    runs = [(m.start(), len(m.group(0))) for m in re.finditer(r"`+", text)]
    i = 0
    while i < len(runs):
        start, n = runs[i]
        if start > 0 and text[start - 1] == "\\":
            # An escape is honoured only OUTSIDE a span, i.e. on an opener,
            # and it takes one backtick; a closer ignores it (inside a span
            # a backslash is just a character).
            start, n = start + 1, n - 1
            if n == 0:
                i += 1
                continue
        for j in range(i + 1, len(runs)):
            if runs[j][1] == n:
                yield start, text[start + n : runs[j][0]]
                i = j + 1
                break
        else:
            i += 1


def findings(text: str, first_line: int = 1) -> List[Tuple[int, str]]:
    """(line, span as written) for each span in *text* holding a run."""
    out = []
    for off, content in spans(text):
        if _RUN.search(content):
            line = first_line + text.count("\n", 0, off)
            out.append((line, content))
    return out


def unreleased(text: str) -> Tuple[int, str]:
    """(first line number, text) of the ``[Unreleased]`` section's body."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(UNRELEASED):
            body = []
            for nxt in lines[i + 1 :]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            return i + 2, "".join(body)
    return 1, ""


def check(root: pathlib.Path) -> List[str]:
    """One ``path:line: `span``` message per finding under *root*."""
    targets: List[Tuple[str, int, str]] = []
    for p in sorted((root / FRAGMENT_DIR).rglob("*.md")):
        targets.append(
            (str(p.relative_to(root)), 1, p.read_text(encoding="utf-8"))
        )
    cl = root / CHANGELOG
    if cl.is_file():
        first, body = unreleased(cl.read_text(encoding="utf-8"))
        targets.append((f"{CHANGELOG} [Unreleased]", first, body))
    errs = []
    for name, first, text in targets:
        for line, content in findings(text, first):
            errs.append(f"{name}:{line}: `{content}`".replace("\n", "\\n"))
    return errs


def main(argv: List[str]) -> int:
    root = pathlib.Path(argv[1] if len(argv) > 1 else ".")
    errs = check(root)
    for e in errs:
        print(e)
    if errs:
        print(
            f"\ncode-span-check: {len(errs)} code span(s) hold a run of "
            "whitespace, which renders verbatim in the release notes.\n"
            "  Usually a span wrapped across a line break that mdformat then "
            "joined with the indent inside it: put the span on one line with "
            "single spaces.\n"
            "  If the spacing is deliberate, show it in a fenced code block "
            "instead (gh-1630).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
