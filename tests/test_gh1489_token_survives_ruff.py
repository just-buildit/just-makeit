"""gh-1489: the scaffolded test's ownership token survives the project's ruff.

The Python half of gh-1448's formatter-survival gate. A downstream runs
`ruff format` and `ruff check --fix` over its tests as a pre-commit fixer;
if either dropped or moved the ``# jm:generated`` line, the next `apply`
would read the file as the author's and stop keeping it in step with the
constructor -- silently, which is the failure gh-1489 exists to remove.

Lives on the PROJECT_ENV_TESTS path because it needs jm's pinned ruff
(gh-1442): on the isolated path `python -m ruff` is not importable, and a
skip there would leave the check disarmed exactly where it has to hold.

GATE: a scaffolded test's ownership token survives the project's formatter.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

from test_gh1489_owned_scaffold_test import (
    TOKEN,
    _add_init_param,
    _project,
    _test_path,
)


def _ruff(proj: Path, *args: str) -> None:
    """jm's pinned ruff over *proj*'s tests, under *proj*'s own config."""
    r = subprocess.run(
        [sys.executable, "-m", "ruff", *args, "--no-cache", "src"],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    # 0 is clean, 1 is findings left after --fix; anything else is ruff not
    # running, which would read as the token having survived nothing.
    assert r.returncode in (0, 1), r.stderr


@pytest.mark.parametrize(
    "fixer",
    [("format",), ("check", "--fix"), ("check", "--fix", "--unsafe-fixes")],
    ids=["format", "check-fix", "check-unsafe-fix"],
)
def test_the_token_survives_ruff(tmp_path, fixer):
    proj = _project(tmp_path)
    path = _test_path(proj, False)
    _ruff(proj, *fixer)
    assert path.read_text().splitlines()[0] == TOKEN
    # gh-1528: the benchmark carries the same token, through the same run.
    bench = proj / "src" / "p" / "benchmarks" / "bench_g.py"
    assert bench.read_text().splitlines()[0] == "# jm:generated bench_g.py"
    # ...and the file is still jm's: a new init param reaches it.
    _add_init_param(proj)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    after = path.read_text()
    assert after.splitlines()[0] == TOKEN
    assert "G(level=3)" in after
