#!/usr/bin/env python3
"""Wait for main's CI on a release commit, then require it certified it.

release.yml's ``verify-ci`` job runs this through ``make release-verify-ci``
before anything is built or published. A release tag must point at a commit
whose ``CI passed`` check (``CI_CHECK_NAME``) concluded ``success`` on main's
push run; this is where that is decided.

The waiting rule (gh-2613)
--------------------------
The question asked on every poll is "is CI for this SHA still ALIVE?", not
"has a fixed window run out?". Each poll reads the newest push run of the CI
workflow (``ci.yml``) for the SHA, through the Actions API rather than the
check-runs list, because a run that is queued behind another one in its
concurrency group has NO jobs and so no check-run at all -- it is invisible
to a check-runs poll, which is how a merely queued run read as "never
started" and then as a timeout.

- **No run for the SHA yet.** Wait at most ``APPEAR_ATTEMPTS x INTERVAL``.
  This is the gh-1327 race (the tag pushed before main's push CI registered)
  and nothing else: measured, a push run registers within seconds of the
  push, so a run that has not appeared in minutes is not coming. Fail
  saying so.
- **The run is live** (``LIVE_STATUSES``: queued, pending, waiting,
  requested, in_progress). Keep waiting, with no cap of our own. A run that
  waits ~1 h for an earlier main run to drain and then runs slowly is
  healthy (v0.127.1 and both v0.128.0 attempts, 2026-11-11), and the old
  60-min window timed out on exactly that and spent ``make ship``'s one
  pre-publish recovery on it. The job's own ``timeout-minutes`` is the only
  backstop, and each waiting line says so, so a job GitHub ends at its
  timeout reads as what it is.
- **The run completed.** Read ``CI passed`` from THAT run's check suite:
  ``success`` proceeds; anything else -- another conclusion, or no such
  check because the run was cancelled before it (a newer push to main can
  supersede a pending run in the group) -- fails naming the run's URL and
  conclusion.

Every exit says which of these happened, and none blames CI for being slow:
a still-running CI is not a failure.

Examples
--------
The pure decision, with a fake API (``gh api`` replies) and no sleeping:

>>> runs = {"workflow_runs": [{"id": 7, "status": "completed",
...     "conclusion": "success", "check_suite_id": 3,
...     "html_url": "https://example.invalid/runs/7"}]}
>>> checks = {"check_runs": [{"name": "CI passed", "status": "completed",
...     "conclusion": "success"}]}
>>> def api(path):
...     return checks if "check-suites" in path else runs
>>> verify("o/r", "abc", api=api, sleep=lambda s: None, say=lambda m: None)
0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable

# One poll every INTERVAL seconds.
INTERVAL = 20
# How long a run may take to APPEAR, in polls. Measured 2026-11-11 over the
# 100 most recent main-push runs of ci.yml: the run's `created_at` is 3-5 s
# after its head commit (median 4 s). 30 x 20 s = 10 min is over a hundred
# times that, room for a GitHub webhook delay, and still short enough that a
# tag on a commit main never ran CI for fails in minutes, not hours.
APPEAR_ATTEMPTS = 30
# A run in any of these is alive: queued behind its concurrency group
# (`pending`), waiting on an environment, or running.
LIVE_STATUSES = frozenset(
    {"queued", "pending", "waiting", "requested", "in_progress"}
)

Api = Callable[[str], Any]


def gh_api(path: str) -> Any:
    """GET one REST path through ``gh api`` and return the parsed JSON.

    ``gh`` carries the token (``GH_TOKEN`` in the workflow) and the retry
    behaviour the rest of release.yml relies on. A failed call raises, which
    fails the job loudly rather than reading an error as "no run yet".
    """
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def newest_run(
    repo: str, sha: str, workflow: str, api: Api
) -> dict[str, Any] | None:
    """The newest push run of ``workflow`` on main for ``sha``, or None.

    Newest first is the API's order. A re-run keeps its run id, so the
    newest run is the one whose attempt is current.
    """
    runs = api(
        f"repos/{repo}/actions/workflows/{workflow}/runs"
        f"?head_sha={sha}&event=push&branch=main&per_page=10"
    ).get("workflow_runs", [])
    return runs[0] if runs else None


def certifying_check(
    repo: str, run: dict[str, Any], check: str, api: Api
) -> dict[str, Any] | None:
    """The ``check`` check-run of ``run``'s own check suite, or None.

    Read from the run's suite, not from every check on the commit, so the
    verdict belongs to the run whose status we waited on.
    """
    name = check.replace(" ", "%20")
    found = api(
        f"repos/{repo}/check-suites/{run['check_suite_id']}/check-runs"
        f"?check_name={name}"
    ).get("check_runs", [])
    return found[0] if found else None


def verify(
    repo: str,
    sha: str,
    *,
    check: str = "CI passed",
    workflow: str = "ci.yml",
    api: Api = gh_api,
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] = lambda m: print(m, flush=True),
) -> int:
    """Wait while CI for ``sha`` is alive, then return 0 if it certified.

    Parameters
    ----------
    repo, sha
        ``owner/name`` and the tagged commit.
    check
        The aggregate check that certifies a tree (``CI_CHECK_NAME``).
    workflow
        The CI workflow's file name.
    api, sleep, say
        The REST reader, the pause between polls and the printer; injected
        so a test can drive every branch without GitHub or a clock.

    Returns
    -------
    int
        0 when ``check`` concluded ``success`` on the SHA's newest push run,
        1 otherwise, after printing a ``::error::`` naming which case it was.
    """
    absent = 0
    waited = 0
    while True:
        run = newest_run(repo, sha, workflow, api)
        if run is None:
            absent += 1
            if absent > APPEAR_ATTEMPTS:
                say(
                    f"::error::No {workflow} push run for {sha} appeared "
                    f"in {APPEAR_ATTEMPTS * INTERVAL // 60} min. A push to "
                    f"main registers its run within seconds, so main never "
                    f"ran CI on this commit: tag a commit that main's CI "
                    f"ran on, or push one."
                )
                return 1
            say(
                f"No {workflow} run for {sha} yet "
                f"({absent}/{APPEAR_ATTEMPTS}); waiting for it to appear..."
            )
        elif run["status"] in LIVE_STATUSES:
            say(
                f"CI run {run['html_url']} is {run['status']} "
                f"({waited // 60} min so far); waiting while it is alive. "
                f"This job's timeout-minutes is the only cap."
            )
        else:
            url = run["html_url"]
            got = certifying_check(repo, run, check, api)
            if got is None:
                say(
                    f"::error::CI run {url} for {sha} completed "
                    f"'{run.get('conclusion')}' with no '{check}' check: it "
                    f"never reached it (a cancelled or superseded run). "
                    f"Refusing to release a tree CI did not certify."
                )
                return 1
            if got.get("conclusion") == "success":
                say(f"'{check}' is green on {url} -- proceeding to release.")
                return 0
            say(
                f"::error::'{check}' concluded '{got.get('conclusion')}' on "
                f"CI run {url} (run concluded '{run.get('conclusion')}'). "
                f"Refusing to release a tree CI did not certify."
            )
            return 1
        sleep(INTERVAL)
        waited += INTERVAL


def main() -> int:
    """Entry point: everything comes in through the environment."""
    return verify(
        os.environ["REPO"],
        os.environ["SHA"],
        check=os.environ.get("CI_CHECK_NAME", "CI passed"),
        workflow=os.environ.get("CI_WORKFLOW", "ci.yml"),
    )


if __name__ == "__main__":
    sys.exit(main())
