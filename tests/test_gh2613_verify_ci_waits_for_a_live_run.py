"""The release's CI-wait waits while CI is alive (gh-1327, gh-2613).

`release.yml`'s verify-ci job refuses to publish a tree main's CI did not
certify. How long it waits has failed twice, in two different ways:

| | window | what it was sized against | what broke it |
| --- | --- | --- | --- |
| gh-1327 | `60 x 20s` = 20 min | nothing | main's CI took 18-33 min |
| gh-2613 | `180 x 20s` = 60 min | CI's DURATION | queue time behind another run |

gh-2613: ci.yml's main concurrency group does not cancel in progress, so a
release commit merged minutes after a code PR waits for that PR's push run
before its own starts. v0.127.1 and both v0.128.0 attempts (2026-11-11) timed
out with CI merely queued, then running, and each spent `make ship`'s one
pre-publish recovery.

The waiting rule now lives in the org standard: `scripts/verify-ci.py`,
vendored by `standard.mk` and run through `make release-verify-ci`, waits as
long as the SHA's CI run is alive, and its behaviour is tested there
(just-buildit.github.io#227). What is jm's own is the WIRING, held here: the
job runs that target and nothing else, its `timeout-minutes` -- the only cap
on a live run -- clears main's measured CI, and the target receives jm's
check name and CI workflow through the environment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github/workflows/release.yml"

# Measured 2026-11-11, `gh run list --workflow=ci.yml --branch main --event
# push -L 60`, created -> completed (queue included) over the 59 completed
# runs: median 41 min, 50 of them under 90, the longest 135. The job's
# timeout is the only cap on a live run, so it must clear the worst with
# room for CI to keep growing.
OBSERVED_MAX_CI_MIN = 135
JOB_TIMEOUT_FLOOR_MIN = 180
# GitHub-hosted runners end any job at 6 h whatever the workflow says, so a
# value above it is not the cap it reads as.
HOSTED_JOB_MAX_MIN = 360


def _job() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
        "verify-ci"
    ]


def test_the_job_timeout_clears_main_s_ci():
    minutes = _job()["timeout-minutes"]
    assert minutes >= JOB_TIMEOUT_FLOOR_MIN, (
        f"verify-ci's timeout-minutes ({minutes}) is under the "
        f"{JOB_TIMEOUT_FLOOR_MIN} min floor; main's CI has been "
        f"measured at {OBSERVED_MAX_CI_MIN} min queue included"
    )
    assert JOB_TIMEOUT_FLOOR_MIN > OBSERVED_MAX_CI_MIN


def test_the_job_timeout_is_one_github_honours():
    assert _job()["timeout-minutes"] <= HOSTED_JOB_MAX_MIN


def test_the_step_runs_the_make_target_and_nothing_else():
    """One implementation: a poll loop inlined in the workflow beside the
    vendored script is the pair that drifts (gh-2613 had both)."""
    steps = [s for s in _job()["steps"] if "run" in s]
    assert [s["run"].strip() for s in steps] == ["make -s release-verify-ci"]


def test_the_target_receives_jm_s_check_and_workflow_from_the_environment():
    """The standard exports CI_CHECK_NAME and CI_WORKFLOW to every recipe,
    rather than spelling them on the script's command line, so the value a
    recipe sees is what the variable holds when it runs. Read from a
    recipe's own environment: a `-n` dry run of the target would show only
    its command line, which no longer carries them."""
    out = subprocess.run(
        [
            "make",
            "-s",
            "--eval",
            "jm-env-probe: ; @printf '%s|%s\\n' \"$$CI_CHECK_NAME\""
            ' "$$CI_WORKFLOW"',
            "jm-env-probe",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.strip().splitlines()[-1] == "CI passed|ci.yml", out
