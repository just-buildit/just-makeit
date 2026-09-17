"""The release's CI-wait must outlast main's CI (gh-1327).

`release.yml`'s "Verify CI already passed for this commit" job polls for a
conclusion on the tagged SHA. It always polled -- the premise in the issue
that it "reads once" was wrong, and checking that first is what found the
real cause. What it did NOT do was poll for long enough:

| | |
| --- | --- |
| poll window | `60 x 20s` = **20 min** |
| main's CI | **18-33 min** over 15 runs, median 24.7 (Coverage is the long pole) |

The window sat BELOW the median, so a release cut straight after a bump
merge -- which is what the documented flow produces -- timed out on its
first attempt. v0.76.0's ran `00:11:06 -> 00:31:38`, 20m32s, exactly
`60 x 20s` plus API latency, and exited on the timeout branch.

Two costs, and the second is why this is not cosmetic:

- `release-watch` reported it as *"likely a flake"*. It is deterministic.
- **It spent the one pre-publish auto-recovery on a known cause.** The rerun
  budget is deliberately ONE, so a genuine pre-publish failure then has no
  recovery left, behind a log calling the first failure a flake.

**Why this is a gate and not a bigger number.** The window is a value that
looks arbitrary and reads as "surely nobody waits an hour", so the pressure
is always to shorten it. It is not arbitrary: it is sized against a measured
distribution, and the only way to know a proposed value is wrong is to
compare it against that distribution. This test is where that comparison
lives, so shortening the window means facing the number rather than
rediscovering v0.76.0.

The floor is deliberately not the max: CI gets slower as the suite grows, and
a window equal to today's worst case is one bad run from failing again.

**The other half of gh-1327 is not gated here, deliberately.**
`release-watch` printing *"likely a flake"* for a failure it can attribute is
a fault in `scripts/release-watch.sh`, which is VENDORED from the cross-org
standard -- editing it here would be a private copy of shared tooling, and
`standard-check` fails `make lint` on exactly that. It goes to canonical
(`just-buildit/just-buildit.github.io`) and comes back through the vendored
copy, with its gate alongside it. This file covers the half that is genuinely
this repo's: `release.yml`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parent.parent / ".github/workflows/release.yml"

# Measured 2026-09-17, `gh run list --workflow=ci.yml --branch main`, the 15
# most recent successful main-push runs:
#
#     18 20 21 21 22 22 25 25 26 27 27 27 29 29 33   (minutes)
#
# min 18.1, median 24.7, max 32.8.
OBSERVED_MAX_MIN = 33
# Enough headroom above the worst observed run that CI has to get ~35% slower
# before this is tight again. A release that waits an extra 20 minutes costs
# nothing (the loop exits on the first conclusion); one that gives up early
# costs the rerun budget and prints a wrong diagnosis.
FLOOR_MIN = 45


def _verify_step() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("Require a green")
    end = text.index("\n  build:", start)
    return text[start:end]


def _int_assignment(name: str, body: str) -> int:
    m = re.search(rf"^\s*{name}=(\d+)\s*$", body, re.M)
    assert m, f"{name} is not a plain integer assignment in the verify step"
    return int(m.group(1))


class TestThePollWindowOutlastsMainsCI:
    def test_the_window_clears_the_floor(self):
        body = _verify_step()
        attempts = _int_assignment("ATTEMPTS", body)
        interval = _int_assignment("INTERVAL", body)
        window_min = attempts * interval / 60
        assert window_min >= FLOOR_MIN, (
            f"the verify job waits {window_min:.0f} min, under the "
            f"{FLOOR_MIN} min floor. main's CI has been measured at up to "
            f"{OBSERVED_MAX_MIN} min, so this races it exactly as gh-1327 "
            f"describes. Raise ATTEMPTS, or re-measure and move the floor "
            f"with the new numbers recorded in this file."
        )

    def test_the_floor_actually_clears_the_measurement(self):
        """Guard the guard. A floor that drifted below the measurement it
        cites would pass the test above while gating nothing."""
        assert FLOOR_MIN > OBSERVED_MAX_MIN, (
            f"the floor ({FLOOR_MIN}) no longer clears the longest observed "
            f"CI run ({OBSERVED_MAX_MIN}); it is documentation, not a gate"
        )


class TestTheLoopStillUsesTheDeclaredValues:
    """The numbers are only a gate if the loop reads them. A hard-coded
    `seq 1 60` beside an unused `ATTEMPTS=180` is exactly the shape that
    passes this file and races anyway."""

    @pytest.mark.parametrize("var", ["ATTEMPTS", "INTERVAL"])
    def test_the_variable_is_referenced(self, var):
        body = _verify_step()
        uses = len(re.findall(rf'"\${var}"', body))
        assert uses >= 1, f"{var} is assigned but never used in the loop"

    def test_no_bare_integer_loop_bound_remains(self):
        body = _verify_step()
        assert not re.search(r"seq 1 \d", body), (
            "the loop bound is a literal again; it must read $ATTEMPTS or "
            "this file gates nothing"
        )
        assert not re.search(r"^\s*sleep \d", body, re.M), (
            "the sleep is a literal again; it must read $INTERVAL"
        )


class TestTheTimeoutMessageDoesNotMisdiagnose:
    """The old message told the reader to tag a green commit, which is what
    they had just done -- so the log blamed the tree for a fault in the
    waiting. Now that the window clears main's CI, a timeout here really is
    a real failure, and the message says so."""

    def test_it_does_not_blame_the_tag(self):
        body = _verify_step()
        timeout_line = body[body.index("Timed out") :]
        assert "Tag a commit merged to main" not in timeout_line, (
            "the timeout message still tells the user to do the thing they "
            "already did"
        )

    def test_it_names_the_window_it_waited(self):
        body = _verify_step()
        assert "$((ATTEMPTS * INTERVAL" in body, (
            "the timeout message should state how long it actually waited, "
            "derived from the values rather than restated"
        )
