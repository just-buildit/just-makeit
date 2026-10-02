"""``make pr-watch`` is the standard's target, running canonical's script (gh-1818).

jm used to define ``pr-watch`` in ``local.mk`` around a hand copy of
``scripts/pr-watch.sh`` that nothing compared to canonical. The copy had
already missed canonical's REPO derivation when canonical gained the
stuck-queued-run detector (gh-1812), which would never have reached jm.

``HAS_PR_WATCH = 1`` now takes both from the standard: the target, and the
script listed in ``VENDORED_FILES``, so ``standard-check`` fails ``make lint``
on any local edit. These tests refuse the two ways back to a fork: dropping
the flag, so the script stops being vendored, and redefining the target in
jm's own makefiles.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = "scripts/pr-watch.sh"


def _make(*args: str, stdin: str = "") -> str:
    return subprocess.run(
        ["make", "-s", "--no-print-directory", *args],
        input=stdin,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_script_is_vendored() -> None:
    # The rule arrives on stdin, not through --eval: macOS ships GNU make
    # 3.81, which has no --eval.
    out = _make(
        "-f",
        "Makefile",
        "-f",
        "-",
        "_jm-vendored",
        stdin="_jm-vendored: ; @echo $(VENDORED_FILES)\n",
    ).split()
    assert out, "make named no VENDORED_FILES"
    assert SCRIPT in out, (
        f"{SCRIPT} is not in VENDORED_FILES, so standard-check no longer "
        "holds it to canonical: is HAS_PR_WATCH = 1 still set?"
    )


def test_the_target_runs_the_vendored_script() -> None:
    out = _make("-n", "pr-watch", "PR=1818")
    assert re.search(r"scripts/pr-watch\.sh '?1818'?", out), out


def test_jm_does_not_define_its_own_pr_watch() -> None:
    rule = re.compile(r"^pr-watch\s*:", re.M)
    own = [
        name
        for name in ("Makefile", "local.mk")
        if rule.search((REPO / name).read_text(encoding="utf-8"))
    ]
    assert not own, (
        f"{own} define pr-watch, overriding the standard's: drop it and "
        "use HAS_PR_WATCH"
    )
