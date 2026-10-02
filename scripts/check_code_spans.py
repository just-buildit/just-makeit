#!/usr/bin/env python3
"""Fail on a whitespace run inside a Markdown code span (gh-1779).

A code span renders its content verbatim, so `` `jm   upgrade` `` reaches
the docs site as exactly that. The run is rarely typed: a span hand-wrapped
across an indented line break gets the indent joined into it when mdformat
reflows the paragraph, and mdformat never rewraps inside a span afterwards,
so ``make format`` keeps it forever (the mechanism is gh-1630's).

gh-1630 gated the changelog: ``scripts/changelog.py`` (vendored from the
standard) refuses a run in a ``changelog.d/`` fragment. The same formatter
runs over every other Markdown file here, and on 2026-10-01 seven spans in
``docs/`` held a run (gh-1779). This widens the gate to every tracked
Markdown file, reusing the standard's one span reader, ``span_runs`` --
CommonMark backtick pairing, fenced blocks skipped -- rather than a second
copy of it.

Two files are left to their owner: ``changelog.d/`` is checked by
``changelog-check`` already, and ``CHANGELOG.md``'s released sections are
history that ``changelog-sections-check`` forbids editing; the runs there
stay as shipped.

A run that is MEANT -- syntax whose spacing is the point -- belongs in a
fenced block, which this skips.

Usage::

    check_code_spans.py [FILE ...]   # no arguments: every tracked .md

Exit 1 naming each span, 0 otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from changelog import span_runs  # noqa: E402  (the vendored primitive)

#: Markdown this gate leaves to the changelog gates (see the docstring).
OWNED_ELSEWHERE = ("CHANGELOG.md", "changelog.d/")


def tracked_markdown() -> List[Path]:
    """Every tracked ``.md`` file, minus the ones the changelog gates own."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        Path(name)
        for name in out.split("\0")
        if name and not name.startswith(OWNED_ELSEWHERE)
    ]


def findings(paths: List[Path]) -> List[str]:
    """One ``file:line: `content``` line per span holding a run.

    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> _ = (d / "a.md").write_text("x `jm\\n    upgrade` y\\n")
    >>> [f.split(": ", 1)[1] for f in findings([d / "a.md"])]
    ['`jm\\\\n    upgrade`']
    >>> _ = (d / "b.md").write_text("```\\n`a  b`\\n```\\n`ok`\\n")
    >>> findings([d / "b.md"])
    []
    """
    out: List[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line, content in span_runs(text):
            shown = content.replace("\n", "\\n")
            out.append(f"{path}:{line}: `{shown}`")
    return out


def main(argv: List[str]) -> int:
    paths = [Path(a) for a in argv] if argv else tracked_markdown()
    found = findings(paths)
    if found:
        print("ERROR: a code span holds a run of whitespace, which renders")
        print("verbatim (gh-1779):")
        for f in found:
            print(f"  {f}")
        print("")
        print("  Usually a span wrapped across a line break that mdformat")
        print("  then joined with the indent inside it: put the span on one")
        print("  line with single spaces. If the spacing is meant, use a")
        print("  fenced block, which this skips.")
        return 1
    print(f"code-span-check: no whitespace run in {len(paths)} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
