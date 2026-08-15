"""Every released version keeps its CHANGELOG section.

`release.yml` builds the GitHub Release body by extracting the `## [x.y.z]`
section for the tag being pushed, stopping at the next `## ` heading. So a
version heading that goes missing does not fail anything at the time — it
silently reassigns that release's bullets to whichever section now encloses
them, and the *next* release publishes them as its own.

That is not hypothetical. Preparing 0.61.1, the extraction for it came back
with four `###` sections and five bullets instead of two and two: the
`## [0.61.0]` heading had been deleted a day earlier by a PR that anchored an
`[Unreleased]` edit on it and did not put it back. v0.61.0's own notes were
already published and correct, so nothing had noticed, and nothing would have
until 0.61.1 shipped describing gh-994/996/998 a second time.

The tags are the SSOT for what has been released, so this compares against
them rather than against a list here. The `## [` anchor at column 1 is the
same one `release.yml`'s awk uses (`index($0, "## [" ver "]") == 1`) — a check
that matched the heading loosely could pass on a file the release step cannot
read.

This shipped with a `_KNOWN_MISSING` ratchet holding the four tags that had no
section — 0.15.1, 0.15.2, 0.19.12, 0.19.14. gh-1007 restored all four and the
ratchet is gone rather than left at zero: an empty exemption list is one more
place a future missing section can be parked, and the test policing it could
then only fail by coincidence.

What the archaeology found is worth keeping, because it says what this failure
mode actually looks like on disk. **None of the four had lost its prose.** The
bullets were all still in `CHANGELOG.md`, sitting under the next heading down —
0.19.12's and 0.19.14's under 0.19.13 and 0.19.15, 0.15.1's and 0.15.2's under
0.15.3, which had carried three `### Added`/`### Fixed` pairs. Restoring them
meant inserting four heading lines and moving no text. So a deleted heading is
not a deletion, it is a **reassignment** — which is why nothing looked wrong for
two months and why comparing against the tags, not against how complete the
file reads, is the check that works.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CHANGELOG = _ROOT / "CHANGELOG.md"

#: `## [1.2.3] — 2026-01-01`, anchored at column 1 exactly as release.yml
#: requires. `[Unreleased]` deliberately does not match.
_SECTION = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.M)


def _released_versions() -> "list[str]":
    """Versions with a `v*` tag, newest first. Empty if git has no tags."""
    proc = subprocess.run(
        ["git", "tag", "--list", "v[0-9]*"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [
        t[1:]
        for t in proc.stdout.split()
        if re.fullmatch(r"v\d+\.\d+\.\d+", t)
    ]


def _sections() -> "list[str]":
    return _SECTION.findall(_CHANGELOG.read_text(encoding="utf-8"))


def test_every_tagged_release_has_a_section():
    tags = _released_versions()
    if not tags:
        pytest.skip("no release tags in this clone")
    have = set(_sections())
    missing = sorted(set(tags) - have)
    assert not missing, (
        f"CHANGELOG.md has no `## [{missing[0]}]` heading, but v{missing[0]} "
        f"is tagged. release.yml extracts a release's notes by that exact "
        f"heading, so the bullets under it now belong to whichever section "
        f"encloses them — and the next release will publish them as its own. "
        f"Missing: {', '.join(missing)}"
    )


def test_no_version_heading_appears_twice():
    """Two headings for one version splits its notes at the first.

    The other half of the same failure: the awk stops at the next `## `, so a
    duplicated heading truncates the release body rather than losing it.
    """
    seen = _sections()
    dupes = sorted({v for v in seen if seen.count(v) > 1})
    assert not dupes, f"duplicated CHANGELOG heading(s): {', '.join(dupes)}"


def test_the_version_being_released_has_a_section():
    """Fail closed on an unpromoted `[Unreleased]`.

    The tag check above cannot see this one: on a release branch the version
    in `pyproject.toml` has no tag yet, so forgetting to promote
    `[Unreleased]` would publish an empty body with nothing to compare it to.
    doppler's `make release-notes` fails closed for the same reason.
    """
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert m, "pyproject.toml has no version"
    version = m.group(1)
    assert version in _sections(), (
        f"pyproject.toml is at {version} but CHANGELOG.md has no "
        f"`## [{version}]` section. Promote `## [Unreleased]` before tagging "
        f"— release.yml would publish an empty release body."
    )


def test_sections_are_in_descending_version_order():
    """Newest first, which is what a reader and the extractor both assume."""

    def key(v: str) -> "tuple[int, ...]":
        return tuple(int(p) for p in v.split("."))

    seen = _sections()
    out_of_order = [(a, b) for a, b in zip(seen, seen[1:]) if key(a) < key(b)]
    assert not out_of_order, (
        f"CHANGELOG sections are not newest-first: {out_of_order[0][0]} "
        f"appears above {out_of_order[0][1]}"
    )
