"""Every whole-tree suite ignores the PROJECT_ENV_TESTS files (gh-2658).

``PROJECT_ENV_TESTS`` are the tests that need the project's own dev
environment -- mypy, the pinned formatters, an installed tree. The suites
that run in ``uv run --isolated`` must ``--ignore`` each one, and
``test_gh1724_array_stub_mypy.py`` fails loudly at import when that is
forgotten, by design. ``test`` carried the ignore and ``test-fast``, declared
beside it, did not: ``make test-fast`` failed at collection on main, and no
CI job runs it, so nothing went red.

GATE: every whole-tree pytest run (``pytest -n auto``; the golden-example
run names its one file) that ``make -n`` prints for each target below
ignores every ``PROJECT_ENV_TESTS`` file -- read from make, both the list
and the command, so the check follows whatever the Makefile says. The
coverage shards run in the dev group and ignore them too: the files run
once, in the examples job, not twice.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

#: The targets that run pytest over the whole tree.
SUITES = ["test", "test-fast", "coverage-shard COVERAGE_SHARD=1"]

pytestmark = pytest.mark.skipif(not shutil.which("make"), reason="no make")


def _make(*args: str) -> str:
    r = subprocess.run(
        ["make", "--no-print-directory", "-s", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _project_env_tests() -> list[str]:
    files = _make(
        "--eval=_gh2658: ; @echo $(PROJECT_ENV_TESTS)", "_gh2658"
    ).split()
    assert "tests/test_gh1724_array_stub_mypy.py" in files, files
    return files


@pytest.mark.parametrize("target", SUITES)
def test_the_suite_ignores_every_project_env_test(target):
    printed = _make("-n", *target.split())
    runs = [
        ln
        for ln in printed.splitlines()
        if re.search(r"\bpytest\b.* -n auto\b", ln)
    ]
    assert runs, f"`make -n {target}` runs no whole-tree pytest:\n{printed}"
    for run in runs:
        missing = [
            f for f in _project_env_tests() if f"--ignore={f}" not in run
        ]
        assert not missing, f"`make {target}` collects {missing}:\n{run}"
