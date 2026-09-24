"""A release bump must not run the full matrix (gh-1327, gh-1537).

`ci.yml`'s `changes` job exists to skip the matrix and Coverage for a
version-string change, and `ci-passed` treats that skip as green. It first
never fired at all -- its hand-written allow-list named ``jb.toml`` after
the rename to ``bootstrap.toml`` -- and then had to grow for the
``changelog.d/`` fragments a release deletes (gh-1526):

| stage | measured, before the fix |
| --- | --- |
| bump-PR CI | 31.2 min |
| main CI on the bump commit | 28.8 min |

The job now calls the standard's ``make ci-changes`` (gh-1537), which
decides by SUBSTITUTION rather than by a list: every changed file except the
changelog must equal its base copy with the old version replaced by the new
one, read through this repo's ``VERSION_PROBES``. So there is no list here to
go stale. What stays jm's to get right, and is what this file tests:

- the workflow really calls the target;
- the target, with jm's own ``VERSION_PROBES``, calls jm's real release
  commits bump-only and a real source commit not;
- ``ci-passed`` still treats the skip as green.

The mechanism itself is tested in canonical's CI, where it is defined.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
CI = REPO / ".github/workflows/ci.yml"

# A merged source change (gh-1363, #1504): not a version bump, so the
# matrix must run. A fixed commit, because "the parent of a release" is not
# guaranteed to be one.
_SOURCE_COMMIT = "5eb3e85"


def _changes_job() -> str:
    text = CI.read_text(encoding="utf-8")
    m = re.search(r"^  changes:\n(.*?)(?=^  [\w-]+:\n)", text, re.M | re.S)
    assert m, "no `changes:` job in ci.yml"
    return m.group(1)


def _ci_changes(commit: str) -> str:
    """``make ci-changes`` for *commit* against its parent, as CI runs it.

    A detached worktree at *commit*, with TODAY's make files copied in: the
    target and ``VERSION_PROBES`` under test are the current ones, and the
    tree they judge is the historical commit.
    """
    tmp = Path(tempfile.mkdtemp())
    wt = tmp / "wt"
    try:
        r = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), commit],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"{commit} not in this clone: {r.stderr.strip()}")
        for f in ("Makefile", "standard.mk", "local.mk"):
            shutil.copy2(REPO / f, wt / f)
        out = subprocess.run(
            ["make", "-s", "ci-changes", f"BASE={commit}^"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=REPO,
            capture_output=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def _release_tags() -> "list[str]":
    out = subprocess.run(
        ["git", "tag", "--sort=-v:refname"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return [t for t in out.stdout.split() if t.startswith("v")][:3]


class TestTheWorkflowUsesTheStandard:
    def test_the_changes_job_calls_make_ci_changes(self):
        assert "make -s ci-changes" in _changes_job()

    def test_no_hand_written_allow_list_is_left(self):
        """The list is what went stale; it must not creep back in."""
        assert "grep -qvE" not in _changes_job(), (
            "the changes job still carries a hand-written file allow-list "
            "beside `make ci-changes`"
        )


class TestJmsHistoryIsClassifiedRight:
    def test_the_recent_release_commits_are_bump_only(self):
        tags = _release_tags()
        if not tags:
            pytest.skip("no release tags in this clone")
        for tag in tags:
            got = _ci_changes(tag)
            assert got == "src=false", (
                f"{tag}'s release commit reads as {got!r}, so it runs the "
                f"full matrix for a version string"
            )

    def test_a_source_change_is_not_bump_only(self):
        assert _ci_changes(_SOURCE_COMMIT) == "src=true"


def test_the_aggregator_still_greens_a_skip():
    """The fast path is only usable because `ci-passed` treats the skip
    as success. If that ever changes, skipping the matrix makes every
    release unmergeable instead of fast."""
    text = CI.read_text(encoding="utf-8")
    # Executed, not string-matched, in test_ci_passed_aggregator.py; this
    # keeps the pointer from the fast path to the rule it depends on.
    assert 'if [[ "$SRC" == "false" ]]; then exit 0; fi' in text, (
        "ci-passed no longer treats a bump-only skip as green"
    )
