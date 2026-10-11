"""The `CI passed` aggregator, executed rather than read.

`CI passed` is the only status the branch ruleset requires, so what its
script accepts is what merges. It failed only on ``failure``, and a job that
hits its ``timeout-minutes`` ends ``cancelled``: three Coverage runs on
``main`` were killed at 30 minutes while ``CI passed`` stayed green, so the
coverage gate gated nothing whenever it was slow.

The script takes its inputs from ``env`` (``SRC``, ``RESULTS``), so this test
lifts the real ``run:`` block out of ci.yml and runs it under bash with each
result a job can end in. A string match would keep passing with the old
condition reworded; running it cannot.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CI = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def _script() -> str:
    """The aggregator's `run:` block, dedented."""
    text = CI.read_text(encoding="utf-8")
    job = text[text.index("\n  ci-passed:") :]
    job = job[: re.search(r"\n  [a-z][\w-]*:\n", job[1:]).start() + 1]
    body = job[job.index("      - run: |\n") + len("      - run: |\n") :]
    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines)


def _exit(src: str, *results: str, docs: str = "", owed: str = "") -> int:
    return subprocess.run(
        ["bash", "-c", _script()],
        env={
            "SRC": src,
            "RESULTS": " ".join(results),
            "DOCS": docs,
            "OWED": owed,
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
    ).returncode


def test_the_script_was_found():
    assert "RESULTS" in _script() and "SRC" in _script()


def test_the_aggregator_reads_its_inputs_from_env():
    """Otherwise the script above is not what GitHub runs."""
    text = CI.read_text(encoding="utf-8")
    job = text[text.index("\n  ci-passed:") :]
    assert "SRC: ${{ needs.changes.outputs.src }}" in job
    assert "RESULTS: ${{ join(needs.*.result, ' ') }}" in job


@pytest.mark.parametrize(
    "results, code",
    [
        (("success", "success", "skipped"), 0),
        (("success", "failure"), 1),
        # The hole: a job killed by its timeout ends `cancelled`.
        (("success", "cancelled"), 1),
        (("success", "neutral"), 1),
    ],
    ids=["green", "failure", "cancelled", "anything-else"],
)
def test_only_success_or_skipped_passes(results, code):
    assert _exit("true", *results) == code


def test_a_bump_only_skip_is_green():
    """src=false skips the matrix on purpose (the release fast path)."""
    assert _exit("false", "skipped", "skipped", "cancelled") == 0


@pytest.mark.parametrize(
    "docs, code",
    [("success", 0), ("skipped", 0), ("failure", 1), ("cancelled", 1)],
)
def test_a_bump_still_needs_its_docs_build(docs, code):
    """gh-1801: under src=false the docs job can still run (a release
    rewrites CHANGELOG.md), and the bump early exit must not certify a
    broken strict build."""
    assert _exit("false", "skipped", docs, docs=docs) == code


def test_the_aggregator_reads_the_docs_result():
    text = CI.read_text(encoding="utf-8")
    job = text[text.index("\n  ci-passed:") :]
    assert "DOCS: ${{ needs.docs.result }}" in job


@pytest.mark.parametrize(
    "owed, code",
    [
        ("success success", 0),
        ("skipped skipped", 0),
        ("success failure", 1),
        ("success cancelled", 1),
        ("failure skipped", 1),
    ],
    ids=["ran", "bump", "leg-failed", "leg-timed-out", "planner-failed"],
)
def test_a_tested_tree_still_needs_the_legs_its_pr_trimmed(owed, code):
    """gh-2125: a push whose tree its PR tested has src=false, and still
    runs the test legs that PR trimmed. This check is what release.yml
    reads, and those legs run nowhere else that gates a release, so the
    early exit must not certify one that failed or timed out -- nor a
    failed planner, which leaves the legs SKIPPED."""
    assert _exit("false", "skipped", owed, docs="skipped", owed=owed) == code


def test_the_aggregator_reads_the_owed_legs():
    text = CI.read_text(encoding="utf-8")
    job = text[text.index("\n  ci-passed:") :]
    assert (
        "OWED: ${{ needs.toolchain.result }} ${{ needs.test.result }}" in job
    )
