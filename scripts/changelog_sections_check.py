#!/usr/bin/env python3
"""Refuse a branch that edits a CHANGELOG section which has already shipped.

gh-1438: PR #1437 put its `### Fixed` entry inside the released
`## [0.82.1]` section instead of under `## [Unreleased]`, and `make lint`
was green, because `changelog-check` asks only whether the branch touched
CHANGELOG.md. It was caught by eye during release prep. Had it merged, the
next release would have shipped without that entry, and the entry would have
stayed under a release that never contained it. A released section is
history, so nothing would ever correct it.

`changelog-check` is right not to judge which changes deserve an entry, and
this does not either. It asks one mechanical question: **is every section
that existed at the merge base, other than `[Unreleased]`, byte-for-byte
unchanged at HEAD?** A section is its `## [...]` heading line plus every
line up to the next `## ` heading.

Comparing sections, not diff hunks, is what lets the release branch pass
with no branch-name carve-out. A release renames `## [Unreleased]` to
`## [x.y.z]` and opens a fresh `[Unreleased]` above it. `[x.y.z]` did not
exist at the base, so it is this branch's own section, and the release is
free to polish it (v0.87.1's release commit added a `### Docs` entry there).
Every section that did exist at the base is still untouched. A diff-hunk
reading would attribute the moved lines to whichever heading git's
alignment happened to pick. That depends on the diff algorithm, not on
what the branch did.

A released section that is gone at HEAD is refused too: deleting history is
the same edit as rewriting it.

**The one edit allowed is putting a section back the way its tag shipped
it.** The rule needs a way out for the mistake it exists to catch.
Otherwise a misplaced entry that got past it (merged before this gate, or on
a branch that skipped lint) could never be corrected. "Put it back" is the
only correction with no judgement in it: the truth of `## [x.y.z]` is the
section as tag `vx.y.z` carried it. #1500 was that case. It merged after
v0.87.1 with its gh-1493 entry inside `[0.87.1]`, and the PR that added
this gate moved the entry out, back to what v0.87.1 shipped.

Inert on main by construction, like `changelog-check`: the merge base is
HEAD, so the two texts are identical.

Usage: changelog_sections_check.py BASE_REV   (compares BASE_REV to HEAD)
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Callable

UNRELEASED = "Unreleased"
_HEADING = re.compile(r"^## \[([^\]]+)\]")


def sections(text: str) -> "dict[str, str]":
    """Map each `## [label]` section of *text* to its full text.

    The text runs from the heading line up to the next `## ` heading, so a
    changed date on the heading counts as an edit, the same as a changed
    entry. Anything above the first heading has no label and is not a
    section.

    Examples
    --------
    >>> s = sections("## [Unreleased]\\n\\n## [1.0] - d\\n\\n- a\\n")
    >>> sorted(s)
    ['1.0', 'Unreleased']
    >>> s["1.0"]
    '## [1.0] - d\\n\\n- a\\n'
    """
    out: dict[str, str] = {}
    label = None
    for line in text.splitlines(keepends=True):
        m = _HEADING.match(line)
        if m:
            label = m.group(1)
            out[label] = line
        elif line.startswith("## "):
            label = None
        elif label is not None:
            out[label] += line
    return out


def _untagged(label: str) -> "str | None":
    return None


def released_edits(
    base: str,
    head: str,
    shipped: "Callable[[str], str | None]" = _untagged,
) -> "list[tuple[str, str]]":
    """Released sections of *base* that *head* changed or removed.

    Returns ``(label, "changed" | "removed")`` pairs in *base*'s order.
    ``[Unreleased]`` is exempt, and so is any section *base* did not have:
    a new section belongs to the branch that adds it. A changed section is
    also exempt when its *head* text is ``shipped(label)``, the section as
    its release tag carried it.

    Examples
    --------
    An entry under `[Unreleased]` passes:

    >>> old = "## [Unreleased]\\n\\n## [1.0]\\n\\n- one\\n"
    >>> released_edits(old, old.replace("]\\n\\n## [1.0]",
    ...                                 "]\\n\\n- new\\n\\n## [1.0]"))
    []

    The same entry put in the released section does not:

    >>> released_edits(old, old + "- new\\n")
    [('1.0', 'changed')]

    A release renames `[Unreleased]` and opens a new one above it:

    >>> released_edits(old, old.replace(
    ...     "## [Unreleased]\\n", "## [Unreleased]\\n\\n## [1.1]\\n"))
    []

    Taking a misplaced entry back out passes only when that restores what
    the tag shipped:

    >>> bad = old + "- new\\n"
    >>> tagged = {"1.0": "## [1.0]\\n\\n- one\\n"}.get
    >>> released_edits(bad, old, tagged)
    []
    >>> released_edits(bad, old)
    [('1.0', 'changed')]
    """
    before = sections(base)
    after = sections(head)
    out = []
    for label, text in before.items():
        if label == UNRELEASED:
            continue
        if label not in after:
            out.append((label, "removed"))
        elif after[label] != text and after[label] != shipped(label):
            out.append((label, "changed"))
    return out


def _show(rev: str) -> str:
    """CHANGELOG.md at *rev*, or ``""`` where it does not exist."""
    r = subprocess.run(
        ["git", "show", f"{rev}:CHANGELOG.md"],
        capture_output=True,
        text=True,
    )
    return r.stdout if r.returncode == 0 else ""


def _shipped(label: str) -> "str | None":
    """Section *label* as its release tag `v<label>` carried it."""
    text = _show(f"v{label}")
    return sections(text).get(label) if text else None


def main(argv: "list[str]") -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    base = argv[0]
    edits = released_edits(_show(base), _show("HEAD"), _shipped)
    if not edits:
        print(
            "changelog-sections-check: no released section edited since "
            f"{base[:12]}"
        )
        return 0
    print("ERROR: this branch edits CHANGELOG sections that already shipped:")
    for label, how in edits:
        print(f"  ## [{label}]  ({how})")
    print(
        "\n  A released section is history. Move the entry under"
        " ## [Unreleased],\n  so the next release carries it. The one edit"
        " allowed is restoring\n  a section to what its v<version> tag shipped."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
