"""A red nightly opens ONE issue, or comments on the open one (gh-2773).

ci.yml's nightly cron is the run that sees a dependency release break jm
with no PR in flight: every ``test`` leg resolves jm's dependencies fresh
(``tests/test_gh2773_suite_ignores_lock.py``). It gates no merge and no
release, so its red went unseen. The ``nightly-red`` job now runs
``make ci-nightly-red`` (``scripts/nightly-red.py``) once ``CI passed`` has
failed on a ``schedule`` run.

Driven here through the make target with ``gh`` faked (the ``GH``
variable, as ``ci-tree-tested.sh``'s tests do), recording every call:

- no open ``nightly-red`` issue: the label is upserted and one issue is
  opened, titled "Nightly CI is red", naming the run and its failed jobs;
- an open one: a comment on it, and nothing created;
- any event but ``schedule``: no ``gh`` call at all;
- in every case, nothing is closed, reopened or edited.

And the wiring: the job follows the aggregator, runs only on a red
schedule run, holds the repository's only ``issues: write``, and runs the
target.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
RUN_ID = "4242"

FAKE_GH = """\
import json, os, sys
args = sys.argv[1:]
stdin = sys.stdin.read() if "-" in args else ""
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "stdin": stdin}) + "\\n")
if args[:1] == ["api"]:
    print("success\\tLint")
    print("failure\\ttest (ubuntu-latest, 3.15)")
    print("cancelled\\tCoverage")
    print("\\tReport a red nightly")
elif args[:2] == ["issue", "list"]:
    print(os.environ.get("FAKE_GH_OPEN", ""))
elif args[:2] == ["issue", "create"]:
    print("https://github.com/o/r/issues/99")
"""


def _run(
    tmp_path: Path, event: str, open_issues: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    """``make -s ci-nightly-red`` under *event*, with ``gh`` faked.

    Every GITHUB_* value is stated, so a CI leg's own (real) ones cannot
    reach the script. *open_issues* is what ``gh issue list`` prints.
    """
    fake = tmp_path / "gh"
    fake.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "GH": str(fake),
        "FAKE_GH_LOG": str(log),
        "FAKE_GH_OPEN": open_issues,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_SHA": "abc123",
    }
    r = subprocess.run(
        ["make", "-s", "ci-nightly-red"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    calls = (
        [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    return r, calls


def _verbs(calls: list[dict[str, Any]]) -> list[list[str]]:
    return [c["args"][:2] for c in calls]


def _never_closes(calls: list[dict[str, Any]]) -> None:
    for c in calls:
        a = c["args"]
        assert a[:1] != ["issue"] or a[1] in ("list", "create", "comment"), a
        assert "--state" not in a or a[:2] == ["issue", "list"], a


def test_no_open_issue_opens_one(tmp_path: Path) -> None:
    r, calls = _run(tmp_path, "schedule")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _verbs(calls) == [
        ["api", "--paginate"],
        ["issue", "list"],
        ["label", "create"],
        ["issue", "create"],
    ], calls
    assert f"actions/runs/{RUN_ID}/jobs" in " ".join(calls[0]["args"])
    listing = calls[1]["args"]
    assert listing[listing.index("--label") + 1] == "nightly-red"
    assert listing[listing.index("--state") + 1] == "open"
    create = calls[3]
    a = create["args"]
    assert a[a.index("--title") + 1] == "Nightly CI is red"
    assert a[a.index("--label") + 1] == "nightly-red"
    text = create["stdin"]
    assert f"https://github.com/o/r/actions/runs/{RUN_ID}" in text
    assert "abc123" in text
    assert "test (ubuntu-latest, 3.15) (failure)" in text
    assert "Coverage (cancelled)" in text
    # Green jobs, and this job itself (no conclusion yet), are not red.
    assert "Lint" not in text and "Report a red nightly" not in text
    _never_closes(calls)


def test_an_open_issue_gets_a_comment(tmp_path: Path) -> None:
    r, calls = _run(tmp_path, "schedule", open_issues="77\n12")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _verbs(calls) == [
        ["api", "--paginate"],
        ["issue", "list"],
        ["issue", "comment"],
    ], calls
    # The oldest open one, when several carry the label.
    assert calls[2]["args"][2] == "12"
    assert f"actions/runs/{RUN_ID}" in calls[2]["stdin"]
    _never_closes(calls)


@pytest.mark.parametrize(
    "event", ["push", "pull_request", "workflow_dispatch", ""]
)
def test_not_the_nightly_calls_no_gh(tmp_path: Path, event: str) -> None:
    r, calls = _run(tmp_path, event)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == []
    assert "nothing to report" in r.stdout


def _ci_jobs() -> dict[str, Any]:
    wf = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    return wf["jobs"]


def test_the_job_follows_a_red_aggregator_on_the_nightly_only() -> None:
    jobs = _ci_jobs()
    job = jobs["nightly-red"]
    assert jobs[job["needs"]]["name"] == "CI passed"
    cond = " ".join(job["if"].split())
    assert cond == (
        "always() && github.event_name == 'schedule' && "
        "needs.ci-passed.result == 'failure'"
    )
    runs = [s.get("run", "") for s in job["steps"]]
    assert "make -s ci-nightly-red" in runs


def test_only_the_reporter_may_write_issues() -> None:
    """``issues: write`` is granted to that one job, in every workflow."""
    writers = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        scopes = [("<workflow>", wf.get("permissions"))] + [
            (jid, j.get("permissions")) for jid, j in wf["jobs"].items()
        ]
        for jid, perms in scopes:
            if perms == "write-all" or (
                isinstance(perms, dict) and perms.get("issues") == "write"
            ):
                writers.append(f"{path.name}:{jid}")
    assert writers == ["ci.yml:nightly-red"]
