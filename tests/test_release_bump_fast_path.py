"""A release bump must not run the full matrix (gh-1327 follow-on).

`ci.yml`'s `changes` job exists to skip the 6x2 matrix and Coverage for a
version-string change, and `ci-passed` already treats that skip as green::

    # Bump-only PRs skip the matrix (src=false) — that is expected and green.
    if [[ "${{ needs.changes.outputs.src }}" == "false" ]]; then exit 0; fi

**It had never once fired.** The allow-list named ``jb.toml``, which this
repo renamed to ``bootstrap.toml`` -- the comment two lines above the regex
already said ``bootstrap.toml``, only the pattern was missed. So every
release bump looked like a source change and ran everything:

| stage | measured |
| --- | --- |
| bump-PR CI | 31.2 min |
| main CI on the bump commit | 28.8 min |
| of which Coverage | 28.4 min |

Roughly an hour of CI per release, almost all of it Coverage running twice
over a changed version string, and it is what made the pre-tag wait in the
release flow necessary at all.

**Why this test derives the file set instead of restating it.** A list
written by hand is what broke: someone renamed the file, updated the prose,
and the regex kept working for a name nothing produces any more. So the
allow-list is checked against the files the repo's own machinery actually
writes during a bump -- read out of `.pre-commit-config.yaml` and the real
release commits -- rather than against four strings typed here. That is the
same rename this repo has already paid for once (`jb.toml` ->
`bootstrap.toml` across six repos, where grepping references missed a file
found by convention).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
CI = REPO / ".github/workflows/ci.yml"
PRECOMMIT = REPO / ".pre-commit-config.yaml"


def _allow_list() -> set[str]:
    """The filenames `ci.yml`'s bump-only check treats as not-source."""
    text = CI.read_text(encoding="utf-8")
    m = re.search(r"grep -qvE '\^\((?P<alts>[^)]+)\)\$'", text)
    assert m, "the bump-only allow-list is no longer a single grep -qvE"
    return {alt.replace("\\", "") for alt in m.group("alts").split("|")}


def _is_source(changed: set[str]) -> bool:
    """Replicate the job's file-set decision: any file outside the list."""
    return bool(changed - _allow_list())


class TestTheAllowListMatchesWhatABumpWrites:
    def test_it_covers_every_file_the_sync_hook_owns(self):
        """`sync-bootstrap-version` names the files it rewrites. Those are
        written during the bump commit, so the allow-list must contain them
        or the fast path cannot fire. This is the assertion that would have
        caught the rename."""
        hook = PRECOMMIT.read_text(encoding="utf-8")
        m = re.search(
            r"id: sync-bootstrap-version.*?files: \^\((?P<alts>[^)]+)\)\$",
            hook,
            re.S,
        )
        assert m, "the sync-bootstrap-version hook no longer declares `files:`"
        owned = {alt.replace("\\", "") for alt in m.group("alts").split("|")}
        missing = owned - _allow_list()
        assert not missing, (
            f"{sorted(missing)} is rewritten by the version-sync hook during "
            f"a release bump but is not in ci.yml's bump-only allow-list, so "
            f"every release runs the full matrix for a version string"
        )

    def test_no_allow_listed_file_is_a_name_nothing_produces(self):
        """The failure was a STALE name, not a missing one -- the pattern
        kept matching a file that no longer exists. An entry matching nothing
        in the repo is the fingerprint."""
        stale = [
            name
            for name in _allow_list()
            if not (REPO / name).exists() and name != "CHANGELOG.md"
        ]
        assert not stale, (
            f"{sorted(stale)} is allow-listed but does not exist in the "
            f"repo -- a renamed file leaves the old name matching nothing "
            f"while the real one forces the full matrix"
        )


class TestRealReleaseCommitsTakeTheFastPath:
    """The end of the argument: the last releases' actual file sets."""

    @staticmethod
    def _tags() -> list[str]:
        out = subprocess.run(
            ["git", "tag", "--sort=-v:refname"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        return [t for t in out.stdout.split() if t.startswith("v")][:3]

    def test_the_recent_release_commits_would_skip(self):
        tags = self._tags()
        if not tags:
            pytest.skip("no release tags in this clone")
        for tag in tags:
            r = subprocess.run(
                ["git", "diff", "--name-only", f"{tag}^", tag],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                pytest.skip(f"{tag} not reachable in this clone")
            changed = {f for f in r.stdout.split() if f}
            assert changed, f"{tag} has an empty diff"
            assert not _is_source(changed), (
                f"{tag}'s bump commit touches {sorted(changed - _allow_list())}"
                f", so it runs the full matrix — about an hour of CI for a "
                f"version string"
            )


class TestItStillRunsEverythingForRealChanges:
    """The fast path must stay narrow. A bump-shaped commit that is not one
    is the thing this must never wave through."""

    @pytest.mark.parametrize(
        "changed",
        [
            {"src/just_makeit/_render.py"},
            {"CHANGELOG.md", "src/just_makeit/_apply.py"},
            {"pyproject.toml", "tests/test_apply.py"},
            {"bootstrap.toml", ".github/workflows/ci.yml"},
        ],
    )
    def test_a_source_change_is_not_bump_only(self, changed):
        assert _is_source(changed), changed

    def test_the_pyproject_guard_is_still_there(self):
        """The file set alone is not enough -- `uv.lock` and `pyproject.toml`
        also move for a dependency change, so the job additionally requires
        that the only pyproject edit is the version line. Losing that would
        let a dependency bump through untested."""
        text = CI.read_text(encoding="utf-8")
        assert "grep -qvE '^[+-]version = '" in text, (
            "the version-line-only guard on pyproject.toml is gone; the "
            "allow-list alone would skip a dependency change"
        )

    def test_the_aggregator_still_greens_a_skip(self):
        """The fast path is only usable because `ci-passed` treats the skip
        as success. If that ever changes, skipping the matrix makes every
        release unmergeable instead of fast."""
        text = CI.read_text(encoding="utf-8")
        # Executed, not string-matched, in test_ci_passed_aggregator.py; this
        # keeps the pointer from the fast path to the rule it depends on.
        assert 'if [[ "$SRC" == "false" ]]; then exit 0; fi' in text, (
            "ci-passed no longer treats a bump-only skip as green"
        )
