"""The release's CI-wait waits while CI is alive (gh-1327, gh-2613).

`release.yml`'s "Verify CI already passed for this commit" job refuses to
publish a tree main's CI did not certify. How long it waits has failed
twice, in two different ways:

| | window | what it was sized against | what broke it |
| --- | --- | --- | --- |
| gh-1327 | `60 x 20s` = 20 min | nothing | main's CI took 18-33 min |
| gh-2613 | `180 x 20s` = 60 min | CI's DURATION | queue time behind another run |

gh-2613: ci.yml's main concurrency group does not cancel in progress, so a
release commit merged minutes after a code PR waits for that PR's push run
before its own starts. v0.127.1 and both v0.128.0 attempts (2026-11-11) timed
out with CI merely queued, then running, printed "longer than main's CI has
ever taken, so this is a real failure" -- false -- and each spent `make
ship`'s one pre-publish recovery.

So the rule is no longer a window. `scripts/verify_ci.py` (run through `make
release-verify-ci`) waits as long as the SHA's CI run is ALIVE, fails early
only when no run appears at all (`APPEAR_ATTEMPTS x INTERVAL`) or one
completes without certifying, and leaves the job's `timeout-minutes` as the
one backstop. This file holds each half against a fake API, and holds both
remaining numbers to the measurements they rest on.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github/workflows/release.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import verify_ci as V  # noqa: E402

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

# Measured 2026-11-11 over the 100 most recent main-push runs of ci.yml:
# `created_at` minus the head commit's timestamp is 3-5 s. The appear window
# must clear that by far more than a webhook delay.
OBSERVED_MAX_APPEAR_S = 5
APPEAR_FLOOR_S = 300

URL = "https://example.invalid/actions/runs/7"


def _run(status: str, conclusion: str | None = None) -> dict[str, Any]:
    return {
        "id": 7,
        "status": status,
        "conclusion": conclusion,
        "check_suite_id": 3,
        "html_url": URL,
    }


class FakeApi:
    """Replays a scripted list of run states, one per poll.

    Each entry is a run dict or None (no run for the SHA yet); the last one
    repeats. ``check`` is what the run's suite reports for ``CI passed``.
    A loop that never ends is a test failure, not a hang: past ``limit``
    polls this raises.
    """

    def __init__(self, states, check=None, limit=5000):
        self.states = list(states)
        self.check = check
        self.limit = limit
        self.polls = 0

    def __call__(self, path: str) -> Any:
        if "check-suites" in path:
            return {"check_runs": [self.check] if self.check else []}
        assert "head_sha=abc" in path and "event=push" in path, path
        i = min(self.polls, len(self.states) - 1)
        self.polls += 1
        if self.polls > self.limit:
            raise AssertionError("verify() polled past any sane bound")
        state = self.states[i]
        return {"workflow_runs": [state] if state else []}


def _verify(api: FakeApi) -> tuple[int, list[str]]:
    said: list[str] = []
    rc = V.verify("o/r", "abc", api=api, sleep=lambda s: None, say=said.append)
    return rc, said


GREEN = {"name": "CI passed", "status": "completed", "conclusion": "success"}
RED = {"name": "CI passed", "status": "completed", "conclusion": "failure"}
# gh-1327's window was 180 polls; a live run outlasting it many times over.
LONG = 2000


class TestALiveRunIsWaitedFor:
    def test_a_long_queue_then_a_long_run_is_waited_out(self):
        """The v0.128.0 shape: queued (no jobs) for longer than the old
        60-min window, then running for longer again, then green."""
        api = FakeApi(
            [_run("pending")] * LONG
            + [_run("queued")] * LONG
            + [_run("in_progress")] * LONG
            + [_run("completed", "success")],
            check=GREEN,
            limit=10 * LONG,
        )
        rc, said = _verify(api)
        assert rc == 0, said[-1]
        assert api.polls == 3 * LONG + 1

    @pytest.mark.parametrize("status", sorted(V.LIVE_STATUSES))
    def test_every_live_status_keeps_waiting(self, status):
        api = FakeApi(
            [_run(status)] * LONG + [_run("completed", "success")],
            check=GREEN,
        )
        assert _verify(api)[0] == 0

    def test_a_waiting_line_names_the_run_and_the_only_cap(self):
        api = FakeApi(
            [_run("queued"), _run("completed", "success")], check=GREEN
        )
        _, said = _verify(api)
        assert URL in said[0] and "queued" in said[0]
        assert "timeout-minutes" in said[0], (
            "a job GitHub ends at its timeout must read as that: the last "
            "line before the cancel has to say the timeout is the cap"
        )


class TestOnlyAnAbsentRunHasAWindow:
    def test_no_run_ever_fails_after_the_appear_window(self):
        api = FakeApi([None])
        rc, said = _verify(api)
        assert rc == 1
        assert api.polls == V.APPEAR_ATTEMPTS + 1
        assert said[-1].startswith("::error::")
        assert "appeared" in said[-1]

    def test_a_run_appearing_inside_the_window_is_followed(self):
        api = FakeApi(
            [None] * V.APPEAR_ATTEMPTS
            + [_run("queued")] * LONG
            + [_run("completed", "success")],
            check=GREEN,
        )
        assert _verify(api)[0] == 0

    def test_the_appear_window_clears_its_measurement(self):
        window_s = V.APPEAR_ATTEMPTS * V.INTERVAL
        assert window_s >= APPEAR_FLOOR_S, (
            f"the appear window is {window_s}s, under the {APPEAR_FLOOR_S}s "
            f"floor; a push run registers in up to {OBSERVED_MAX_APPEAR_S}s "
            f"measured, plus whatever a webhook delay adds"
        )
        assert APPEAR_FLOOR_S > OBSERVED_MAX_APPEAR_S


class TestACompletedRunIsJudgedByItsCheck:
    def test_green_proceeds(self):
        rc, said = _verify(
            FakeApi([_run("completed", "success")], check=GREEN)
        )
        assert rc == 0 and URL in said[-1]

    def test_a_red_check_fails_naming_the_run_and_conclusion(self):
        rc, said = _verify(FakeApi([_run("completed", "failure")], check=RED))
        assert rc == 1
        assert said[-1].startswith("::error::")
        assert URL in said[-1] and "'failure'" in said[-1]

    def test_a_run_that_never_reached_the_check_fails_naming_it(self):
        """A pending run superseded by a newer push to main is cancelled
        before `CI passed` exists."""
        rc, said = _verify(FakeApi([_run("completed", "cancelled")]))
        assert rc == 1
        assert URL in said[-1] and "'cancelled'" in said[-1]


class TestNoMessageClaimsCIHasNeverTakenThisLong:
    def test_the_false_diagnosis_is_gone(self):
        source = (ROOT / "scripts/verify_ci.py").read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for text in (source, workflow):
            assert "ever taken" not in text


def _job() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
        "verify-ci"
    ]


class TestTheJobIsTheBackstop:
    def test_the_job_timeout_clears_main_s_ci(self):
        minutes = _job()["timeout-minutes"]
        assert minutes >= JOB_TIMEOUT_FLOOR_MIN, (
            f"verify-ci's timeout-minutes ({minutes}) is under the "
            f"{JOB_TIMEOUT_FLOOR_MIN} min floor; main's CI has been "
            f"measured at {OBSERVED_MAX_CI_MIN} min queue included"
        )
        assert JOB_TIMEOUT_FLOOR_MIN > OBSERVED_MAX_CI_MIN

    def test_the_job_timeout_is_one_github_honours(self):
        assert _job()["timeout-minutes"] <= HOSTED_JOB_MAX_MIN

    def test_the_appear_window_ends_before_the_job(self):
        """Else a never-appearing run ends as a bare cancel, not the error
        that says no run appeared."""
        window_min = V.APPEAR_ATTEMPTS * V.INTERVAL / 60
        assert _job()["timeout-minutes"] > window_min

    def test_the_step_runs_the_make_target_and_nothing_else(self):
        """One implementation: a poll loop inlined in the workflow beside
        the script is the pair that drifts."""
        steps = [s for s in _job()["steps"] if "run" in s]
        assert [s["run"].strip() for s in steps] == [
            "make -s release-verify-ci"
        ]

    def test_the_target_runs_the_script(self):
        out = subprocess.run(
            ["make", "-s", "-n", "release-verify-ci"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "scripts/verify_ci.py" in out
        assert "CI_CHECK_NAME='CI passed'" in out
        assert "CI_WORKFLOW='ci.yml'" in out
