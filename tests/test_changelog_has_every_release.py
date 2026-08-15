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


#: Tagged releases with no CHANGELOG section, frozen as of the day this gate
#: was written. A ratchet, not an exemption: it may only shrink.
#:
#: The first two are not merely untidy — **v0.15.1 and v0.15.2 published
#: GitHub Releases with empty bodies** (0 chars, checked against the API),
#: which is precisely what a missing section does to `release.yml`'s awk. The
#: other two have bodies, so their notes came from somewhere and the sections
#: were lost afterwards. Restoring all four is archaeology, tracked separately;
#: what this gate is for is stopping the FIFTH.
_KNOWN_MISSING = frozenset({"0.15.1", "0.15.2", "0.19.12", "0.19.14"})


def test_every_tagged_release_has_a_section():
    tags = _released_versions()
    if not tags:
        pytest.skip("no release tags in this clone")
    have = set(_sections())
    missing = sorted(set(tags) - have - _KNOWN_MISSING)
    assert not missing, (
        f"CHANGELOG.md has no `## [{missing[0]}]` heading, but v{missing[0]} "
        f"is tagged. release.yml extracts a release's notes by that exact "
        f"heading, so the bullets under it now belong to whichever section "
        f"encloses them — and the next release will publish them as its own. "
        f"Missing: {', '.join(missing)}"
    )


def test_the_ratchet_only_shrinks():
    """A restored section must leave `_KNOWN_MISSING`.

    Without this the ratchet is write-only: someone fixes 0.15.1, the entry
    stays, and the gate quietly stops covering a version it now could. The
    same shape as gh-991's stale `status_allow` entries — an exemption that
    suppresses nothing still reads as one.
    """
    restored = sorted(_KNOWN_MISSING & set(_sections()))
    assert not restored, (
        f"{', '.join(restored)} now has a CHANGELOG section — remove it from "
        f"_KNOWN_MISSING so the gate covers it."
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
