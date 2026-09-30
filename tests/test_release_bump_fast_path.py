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
WF = REPO / ".github/workflows"
CI = WF / "ci.yml"
CHANGES = WF / "changes.yml"

# A merged source change (gh-1363, #1504): not a version bump, so the
# matrix must run. A fixed commit, because "the parent of a release" is not
# guaranteed to be one.
_SOURCE_COMMIT = "5eb3e85"


def _changes_job() -> str:
    """The one bump-only decision, which every workflow calls (reusable)."""
    return CHANGES.read_text(encoding="utf-8")


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


def _release_commit(tag: str) -> str:
    """The commit that made *tag*'s version -- its release commit.

    NOT the tag's own commit. Before release tags became immutable (the
    `release tags` ruleset, 2026-09-25) a release that failed before publish
    was re-cut on the SAME number, moving the tag onto the commit that fixed
    the defect -- a source change by construction. v0.90.0 is that case: its tag is on #1631, a workflow fix, and its
    release commit is the bump before it. The property this file guards is
    that the BUMP takes the fast path, so the bump is what is read -- the
    newest commit reachable from the tag that wrote ``version = "<v>"``
    into ``pyproject.toml``.
    """
    version = tag[1:]
    out = subprocess.run(
        [
            "git",
            "log",
            "-1",
            "--format=%H",
            f'-Sversion = "{version}"',
            tag,
            "--",
            "pyproject.toml",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    commit = out.stdout.strip()
    assert commit, f"no commit reachable from {tag} sets version {version}"
    return commit


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
            got = _ci_changes(_release_commit(tag))
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


# ── Every workflow a release reaches takes the fast path ─────────────────────
#
# The fast path lived in ci.yml alone. docs.yml, docker.yml and nco_tone_ci.yml
# filtered on hand-written `paths:` lists naming pyproject.toml, so a release
# PR built the docs site, two multi-arch images and the end-to-end example for
# a version string; nco_tone_ci.yml's push-side `paths-ignore` named four bump
# files and missed the changelog.d/ fragments a release deletes, so it ran on
# every release push to main too. The file set is read off the newest release
# commit and the workflows are globbed, so neither can go stale.


def _release_files() -> "list[str]":
    """Every path the newest ``chore: release v`` commit on HEAD touched."""
    sha = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--grep=^chore: release v"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert sha, "no `chore: release v` commit reachable from HEAD"
    out = subprocess.run(
        ["git", "show", "--format=", "--name-only", sha],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert "pyproject.toml" in out, (sha, out)
    return out


def _glob(pattern: str, path: str) -> bool:
    """GitHub's `paths` glob: ``**`` crosses ``/``, ``*`` does not."""
    rx = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            rx += ".*"
            i += 2
        elif pattern[i] == "*":
            rx += "[^/]*"
            i += 1
        else:
            rx += re.escape(pattern[i])
            i += 1
    return re.fullmatch(rx, path) is not None


def _reached(trigger: "dict | None", files: "list[str]") -> bool:
    """Would a push/PR changing exactly *files* start this trigger?"""
    if trigger is None:
        return True
    if "tags" in trigger and "branches" not in trigger:
        return False
    if "paths" in trigger:
        return any(_glob(p, f) for p in trigger["paths"] for f in files)
    if "paths-ignore" in trigger:
        ign = trigger["paths-ignore"]
        return any(not any(_glob(p, f) for p in ign) for f in files)
    return True


def _workflows_a_release_reaches() -> "list[tuple[str, dict]]":
    import yaml

    files = _release_files()
    out = []
    for wf in sorted(WF.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        on = doc.get(True, doc.get("on")) or {}
        if isinstance(on, (str, list)):
            on = {e: None for e in ([on] if isinstance(on, str) else on)}
        if any(
            e in on and _reached(on[e], files)
            for e in ("push", "pull_request")
        ):
            out.append((wf.name, doc["jobs"]))
    return out


def _ungated(jobs: dict) -> "list[str]":
    """Jobs that would run on a bump alone.

    A job is gated when it reads `needs.changes.outputs.src` itself, or needs
    a gated job without an `if` that runs it anyway (`always()`,
    `!cancelled()`), since a skipped need skips its dependents.
    """
    import json

    gated: "set[str]" = {"changes"}
    changed = True
    while changed:
        changed = False
        for name, job in jobs.items():
            if name in gated:
                continue
            body = json.dumps(job)
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            cond = str(job.get("if", ""))
            forced = "always()" in cond or "cancelled()" in cond
            if "needs.changes.outputs.src" in body or (
                any(n in gated and n != "changes" for n in needs)
                and not forced
            ):
                gated.add(name)
                changed = True
    return sorted(set(jobs) - gated)


def test_a_release_reaches_some_workflow():
    """The walk below must have something to walk."""
    names = [n for n, _ in _workflows_a_release_reaches()]
    assert "ci.yml" in names, names


@pytest.mark.parametrize(
    "name,jobs",
    _workflows_a_release_reaches(),
    ids=[n for n, _ in _workflows_a_release_reaches()],
)
def test_every_job_a_release_reaches_is_gated_on_changes(name, jobs):
    uses = (jobs.get("changes") or {}).get("uses", "")
    assert uses == "./.github/workflows/changes.yml", (
        f"{name} is started by a release commit and has no `changes` job "
        "calling ./.github/workflows/changes.yml"
    )
    assert not _ungated(jobs), (
        f"{name}: {_ungated(jobs)} run on a version bump alone -- gate them "
        "on `needs.changes.outputs.src == 'true'`"
    )
