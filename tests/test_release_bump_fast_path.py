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
- ``ci-passed`` still treats the skip as green;
- on a push, the skip holds only over a base whose own ``CI passed`` is
  completed and successful, failing safe on anything else (gh-1763);
- every caller grants the token permissions that check needs.

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
# A merged docs-only change (gh-1801 item 3): one page under docs/, nothing
# else, so ci-docs must answer code=false.
_DOCS_COMMIT = "9762df4"


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
    # gh-1801: the early exit now also requires the docs build, which a
    # bump can still run; a skipped or absent one is still green.
    assert 'if [[ "$SRC" == "false" ]]; then' in text, (
        "ci-passed no longer treats a bump-only skip as green"
    )
    assert 'success | skipped | "") exit 0 ;;' in text, (
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


# ── The fast path holds only over a base main certified (gh-1763) ────────────
#
# ci.yml's concurrency group keeps ONE pending run per group on main, so a
# third push cancels the second's pending run and that commit never runs CI.
# Cutting v0.96.0, #1759's merge (07f09d2) was superseded that way and has no
# "CI passed" at all; the release commit on top was bump-only, so changes.yml
# said src=false and its "CI passed" would have certified code main never
# ran. On a push, the fast path now also needs the base's own "CI passed" to
# be completed and successful; anything else, an API error included, runs
# everything.
#
# These run the `check` step's exact `run:` text, as GitHub does (`bash -e`),
# in a worktree at a real commit, with a fake `gh` on PATH that serves a
# check-runs payload -- so the API's JSON shape is filtered by the real `jq`
# line and the diff verdict comes from the real `make ci-changes`.

_REPO_SLUG = "just-buildit/just-makeit"

# Routed by endpoint, and `--jq` applied with the real jq as gh does, so
# ci-tree-tested's own calls (gh-1801) see the API's shapes too. An endpoint
# with no body configured answers 404, as an unknown one would.
_FAKE_GH = """#!/bin/sh
# One line per call, whatever the --jq argument's own newlines.
printf '%s' "$*" | tr '\\n' ' ' >> "$FAKE_GH_LOG"; echo >> "$FAKE_GH_LOG"
[ -n "$FAKE_GH_FAIL" ] && { echo "HTTP 502" >&2; exit 1; }
jq_expr=""; ep=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) jq_expr="$2"; shift 2 ;;
    -X|-f) shift 2 ;;
    api|--paginate) shift ;;
    *) ep="$1"; shift ;;
  esac
done
case "$ep" in
  */pulls) body="$FAKE_GH_PULLS" ;;
  */git/commits/*) body="$FAKE_GH_COMMIT" ;;
  */compare/*) body="$FAKE_GH_COMPARE" ;;
  *) body="$FAKE_GH_JSON" ;;
esac
[ -n "$body" ] && [ -s "$body" ] || { echo "HTTP 404" >&2; exit 1; }
if [ -n "$jq_expr" ]; then jq -r "$jq_expr" "$body"; else cat "$body"; fi
"""

# ci-tree-tested's endpoints; every OTHER call the step makes reads the
# base's checks.
_TREE_CALL = re.compile(r"/pulls\b|/git/commits/|/compare/|check_name=")


def _check_step() -> dict:
    import yaml

    doc = yaml.safe_load(_changes_job())
    (step,) = [
        s for s in doc["jobs"]["changes"]["steps"] if s.get("id") == "check"
    ]
    return step


def _newest_release_commit() -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%H", "--grep=^chore: release v"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _rev(rev: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", rev],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture(scope="module")
def checkouts():
    """Detached worktrees at the newest release commit and at a source
    commit, with today's make files, plus a bin dir holding the fake gh."""
    tmp = Path(tempfile.mkdtemp())
    made = []
    try:
        trees = {}
        for key, commit in (
            ("bump", _newest_release_commit()),
            ("source", _SOURCE_COMMIT),
            ("docs", _DOCS_COMMIT),
        ):
            wt = tmp / key
            r = subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt), commit],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                pytest.skip(f"{commit} not in this clone: {r.stderr.strip()}")
            made.append(wt)
            for f in ("Makefile", "standard.mk", "local.mk"):
                shutil.copy2(REPO / f, wt / f)
            # The helpers `make ci-tree-tested` and `make ci-docs` run;
            # these commits predate both.
            for helper in ("ci-tree-tested.sh", "ci-docs.py"):
                shutil.copy2(
                    REPO / "scripts" / helper, wt / "scripts" / helper
                )
            trees[key] = (wt, _rev(f"{commit}^"))
        bindir = tmp / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(_FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        yield trees, bindir, tmp
    finally:
        for wt in made:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=REPO,
                capture_output=True,
            )
        shutil.rmtree(tmp, ignore_errors=True)


def _payload(*runs: "tuple[str, str, str | None]") -> str:
    import json

    return json.dumps(
        {
            "total_count": len(runs),
            "check_runs": [
                {"name": n, "status": s, "conclusion": c} for n, s, c in runs
            ],
        }
    )


_OTHERS = (("Lint", "completed", "success"), ("Docs", "completed", "success"))


def _decide(
    checkouts,
    tree: str,
    event: str,
    payload: "str | None",
    tree_api: "dict[str, str] | None" = None,
    want_tested: "str | None" = None,
    outputs: "dict[str, str] | None" = None,
):
    """Run the step; return (src, the gh calls it made for the base).

    ``tree_api`` serves ci-tree-tested's endpoints (``pulls``, ``commit``,
    ``compare``); absent, each answers 404 and the helper says
    tested=false. ``want_tested`` asserts the ``tested`` output.
    """
    import os

    trees, bindir, tmp = checkouts
    wt, base = trees[tree]
    out = tmp / "github_output"
    out.write_text("", encoding="utf-8")
    log = tmp / "gh.log"
    log.write_text("", encoding="utf-8")
    body = tmp / "gh.json"
    body.write_text(payload or "", encoding="utf-8")
    bodies = {}
    for kind in ("pulls", "commit", "compare"):
        f = tmp / f"gh-{kind}.json"
        f.write_text((tree_api or {}).get(kind, ""), encoding="utf-8")
        bodies[f"FAKE_GH_{kind.upper()}"] = str(f)
    step = _check_step()
    env = dict(os.environ)
    env.update({k: str(v) for k, v in step["env"].items()})
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
            "GH_TOKEN": "fake",
            "EVENT": event,
            "REF": (
                "refs/heads/main" if event == "push" else "refs/pull/1/merge"
            ),
            "REPO": _REPO_SLUG,
            "PR_BASE": base if event == "pull_request" else "",
            "BEFORE": base if event == "push" else "",
            "GITHUB_OUTPUT": str(out),
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_JSON": str(body),
            **bodies,
        }
    )
    env.pop("FAKE_GH_FAIL", None)
    if payload is None:
        env["FAKE_GH_FAIL"] = "1"
    script = tmp / "step.sh"
    script.write_text(step["run"], encoding="utf-8")
    r = subprocess.run(
        ["bash", "-e", str(script)],
        cwd=wt,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    written = out.read_text(encoding="utf-8")
    srcs = re.findall(r"^src=(\w+)$", written, re.M)
    assert len(srcs) == 1, (srcs, r.stderr)
    if want_tested is not None:
        # "" means the step must not have asked at all.
        want = [want_tested] if want_tested else []
        got = re.findall(r"^tested=(\w+)$", written, re.M)
        assert got == want, (written, r.stderr)
    for key, value in (outputs or {}).items():
        got = re.findall(rf"^{key}=(\w+)$", written, re.M)
        assert got == [value], (key, written, r.stderr)
    calls = [
        c
        for c in log.read_text(encoding="utf-8").splitlines()
        if not _TREE_CALL.search(c)
    ]
    for call in calls:
        assert f"repos/{_REPO_SLUG}/commits/{base}/check-runs" in call, call
    return srcs[0], calls


class TestTheFastPathNeedsACertifiedBase:
    def test_the_step_reads_the_aggregator_by_its_real_name(self):
        import yaml

        doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
        names = {j.get("name") for j in doc["jobs"].values()}
        assert _check_step()["env"]["CI_CHECK"] in names, (
            "changes.yml asks for a check ci.yml does not produce, so no "
            "base would ever read as certified"
        )

    def test_a_bump_over_a_green_base_takes_the_fast_path(self, checkouts):
        payload = _payload(*_OTHERS, ("CI passed", "completed", "success"))
        src, calls = _decide(checkouts, "bump", "push", payload)
        assert src == "false"
        assert calls, "the base's checks were never asked for"

    def test_a_green_base_found_on_a_later_page(self, checkouts):
        """`gh api --paginate` concatenates one object per page."""
        payload = _payload(*_OTHERS) + _payload(
            ("CI passed", "completed", "success")
        )
        assert _decide(checkouts, "bump", "push", payload)[0] == "false"

    @pytest.mark.parametrize(
        "status,conclusion",
        [
            ("completed", "cancelled"),
            ("completed", "failure"),
            ("completed", "skipped"),
            ("completed", "timed_out"),
            ("in_progress", None),
            ("queued", None),
        ],
    )
    def test_a_bump_over_an_uncertified_base_runs_everything(
        self, checkouts, status, conclusion
    ):
        payload = _payload(*_OTHERS, ("CI passed", status, conclusion))
        assert _decide(checkouts, "bump", "push", payload)[0] == "true"

    def test_a_base_with_no_ci_passed_runs_everything(self, checkouts):
        """07f09d2's case: a superseded pending run leaves no check."""
        payload = _payload(*_OTHERS)
        assert _decide(checkouts, "bump", "push", payload)[0] == "true"

    def test_an_api_error_runs_everything(self, checkouts):
        assert _decide(checkouts, "bump", "push", None)[0] == "true"

    def test_an_unreadable_answer_runs_everything(self, checkouts):
        assert _decide(checkouts, "bump", "push", "<html>")[0] == "true"

    def test_a_source_change_over_a_green_base_still_runs(self, checkouts):
        payload = _payload(("CI passed", "completed", "success"))
        assert _decide(checkouts, "source", "push", payload)[0] == "true"

    def test_a_pr_is_decided_by_its_diff_alone(self, checkouts):
        """A PR's own run covers its diff: no base lookup, as before."""
        src, calls = _decide(checkouts, "bump", "pull_request", None)
        assert (src, calls) == ("false", [])
        src, calls = _decide(checkouts, "source", "pull_request", None)
        assert (src, calls) == ("true", [])


# ── A reusable workflow's token is capped by its caller's ────────────────────
#
# changes.yml asks for `checks: read`. A called job asking for more than its
# caller grants fails the WHOLE workflow before any job starts, and this
# repo's default token (restricted) grants no `checks` -- so every caller,
# down every chain (release.yml -> docker.yml -> changes.yml), must grant it.

_LEVEL = {"none": 0, "read": 1, "write": 2}
# GitHub's "restricted" default, which this repo uses
# (`gh api repos/just-buildit/just-makeit/actions/permissions/workflow`).
_RESTRICTED = {"contents": "read", "packages": "read", "metadata": "read"}


def _perms(p) -> "dict[str, str]":
    if p in ("read-all", "write-all"):
        return {k: p[:-4] for k in ("checks", "contents", "packages")}
    return dict(p or {})


def _workflow(name: str) -> dict:
    import yaml

    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _needs(name: str) -> "dict[str, str]":
    """The most any job in *name* (or anything it calls) asks for."""
    want: "dict[str, str]" = {}
    for job in _workflow(name)["jobs"].values():
        uses = job.get("uses", "")
        asks = (
            _needs(uses.rsplit("/", 1)[1])
            if uses.startswith("./.github/workflows/")
            else _perms(job.get("permissions"))
        )
        for k, v in asks.items():
            if _LEVEL[v] > _LEVEL[want.get(k, "none")]:
                want[k] = v
    return want


def _local_calls() -> "list[tuple[str, str, str]]":
    out = []
    for wf in sorted(WF.glob("*.yml")):
        for jname, job in _workflow(wf.name)["jobs"].items():
            uses = job.get("uses", "")
            if uses.startswith("./.github/workflows/"):
                out.append((wf.name, jname, uses.rsplit("/", 1)[1]))
    return out


def test_changes_yml_asks_for_checks():
    """The walk below must have something to find."""
    assert _needs("changes.yml").get("checks") == "read"


@pytest.mark.parametrize(
    "caller,job,callee",
    _local_calls(),
    ids=[f"{c}:{j}" for c, j, _ in _local_calls()],
)
def test_every_caller_grants_what_its_callee_asks(caller, job, callee):
    doc = _workflow(caller)
    spec = doc["jobs"][job]
    if "permissions" in spec:
        grant = _perms(spec["permissions"])
    elif "permissions" in doc:
        grant = _perms(doc["permissions"])
    else:
        grant = dict(_RESTRICTED)
    short = {
        k: v
        for k, v in _needs(callee).items()
        if _LEVEL[v] > _LEVEL[grant.get(k, "none")]
    }
    assert not short, (
        f"{caller}'s `{job}` calls {callee}, which asks for {short} beyond "
        f"what the caller grants ({grant}); GitHub refuses to start the run"
    )


# ── A tree its merged PR already tested is not run again (gh-1801) ───────────
#
# Each tree that lands on main is tested once: a push whose tree IS a merged
# PR's head, which contains the tip the push replaced and passed "CI passed"
# from GitHub Actions, skips the matrix like a bump does. The verdict is the
# canonical `make ci-tree-tested`, run here for real against the fake API;
# every case where one condition fails must fall through to the normal path,
# which for a source commit is the full run.

_PR_HEAD = "f" * 40


def _head_tree(checkouts, key: str) -> str:
    trees, _, _ = checkouts
    return subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=trees[key][0],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _tree_api(tree_sha: str, compare: str = "ahead") -> "dict[str, str]":
    import json

    return {
        "pulls": json.dumps(
            [{"merged_at": "2026-10-02T00:00:00Z", "head": {"sha": _PR_HEAD}}]
        ),
        "commit": json.dumps({"tree": {"sha": tree_sha}}),
        "compare": json.dumps({"status": compare}),
    }


def _pr_checks(conclusion: str, slug: str = "github-actions") -> str:
    import json

    return json.dumps(
        {
            "total_count": 1,
            "check_runs": [
                {
                    "name": "CI passed",
                    "status": "completed",
                    "conclusion": conclusion,
                    "app": {"slug": slug},
                    "started_at": "2026-10-02T00:00:00Z",
                }
            ],
        }
    )


class TestATestedTreeIsNotRunAgain:
    def test_a_source_push_whose_pr_passed_skips_without_a_diff(
        self, checkouts
    ):
        src, calls = _decide(
            checkouts,
            "source",
            "push",
            _pr_checks("success"),
            tree_api=_tree_api(_head_tree(checkouts, "source")),
            want_tested="true",
        )
        assert src == "false"
        assert calls == [], "a tested tree needs no base or diff verdict"

    def test_a_pr_that_already_had_the_tip_is_enough(self, checkouts):
        src, _ = _decide(
            checkouts,
            "source",
            "push",
            _pr_checks("success"),
            tree_api=_tree_api(_head_tree(checkouts, "source"), "identical"),
            want_tested="true",
        )
        assert src == "false"

    @pytest.mark.parametrize(
        "why,api,checks",
        [
            ("another tree", lambda t: _tree_api("0" * 40), "success"),
            (
                "branch moved under it",
                lambda t: _tree_api(t, "diverged"),
                "success",
            ),
            ("its CI failed", lambda t: _tree_api(t), "failure"),
            (
                "not from GitHub Actions",
                lambda t: _tree_api(t),
                "success-other",
            ),
            ("no merged PR", lambda t: {}, "success"),
        ],
    )
    def test_any_unmet_condition_runs_everything(
        self, checkouts, why, api, checks
    ):
        payload = (
            _pr_checks("success", slug="someone-else")
            if checks == "success-other"
            else _pr_checks(checks)
        )
        src, _ = _decide(
            checkouts,
            "source",
            "push",
            payload,
            tree_api=api(_head_tree(checkouts, "source")),
            want_tested="false",
        )
        assert src == "true", why

    def test_a_pull_request_never_asks(self, checkouts):
        src, _ = _decide(
            checkouts,
            "source",
            "pull_request",
            _pr_checks("success"),
            tree_api=_tree_api(_head_tree(checkouts, "source")),
            want_tested="",
        )
        assert src == "true"


# ── A docs-only diff skips what docs cannot break (gh-1801 item 3) ──────────
#
# `make ci-docs`, run by the step itself after ci-changes, writes docs= and
# code=. ci.yml skips the jobs docs cannot break only on an explicit
# code=false, so the step must produce exactly that for a docs-only diff, and
# code=true for anything else.


class TestADocsOnlyDiffIsClassified:
    def test_a_docs_only_pr(self, checkouts):
        src, _ = _decide(
            checkouts,
            "docs",
            "pull_request",
            _payload(*_OTHERS),
            outputs={"docs": "true", "code": "false"},
        )
        assert src == "true", "a docs change is not a version bump"

    def test_a_source_pr_is_code(self, checkouts):
        _decide(
            checkouts,
            "source",
            "pull_request",
            _payload(*_OTHERS),
            outputs={"code": "true"},
        )
