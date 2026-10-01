"""gh-1777: what a refusal does to a REAL interpreter's stderr and exit code.

``run_cli`` turns an exception escaping ``main()`` into a printed traceback
and exit 1 by itself, so in-process it cannot show what the interpreter does
-- the traceback and the code under test would be the harness's. These run
the console script's entry point in a child.

GATE: in a real process a load refusal prints one ``error:`` line, no
traceback, exit 1; ``JM_DEBUG=1`` restores the traceback; a plain
``ValueError`` from jm's own code still tracebacks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

SRC = Path(__file__).parent.parent / "src"

#: What ``[project.scripts] jm`` calls, run the way the console script runs
#: it. *patch* runs first, so a bug can be planted in the child.
ENTRY = "{patch}\nfrom just_makeit._cli import main\nmain()\n"

BUG = (
    "import just_makeit._config as C\n"
    "def boom(*a, **k):\n"
    "    raise ValueError('gh1777 genuine bug')\n"
    "C.load = boom\n"
)


def _jm(cwd: Path, *args: str, patch: str = "", debug: str = ""):
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    env.pop("JM_DEBUG", None)
    if debug:
        env["JM_DEBUG"] = debug
    return subprocess.run(
        [sys.executable, "-c", ENTRY.format(patch=patch), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture(scope="module")
def refused(tmp_path_factory) -> Path:
    where = tmp_path_factory.mktemp("gh1777_child")
    assert run_cli("new", "proj", cwd=where).returncode == 0
    root = where / "proj"
    assert run_cli("object", "gen", cwd=root).returncode == 0
    with (root / "just-makeit.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[[gen.methods]]\nname = "foo"\n')
    return root


def test_a_refusal_exits_1_with_one_error_line(refused: Path) -> None:
    r = _jm(refused, "apply")
    assert r.returncode == 1, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1, r.stderr
    assert "'gen' already exists" in errors[0]


def test_jm_debug_restores_the_traceback(refused: Path) -> None:
    r = _jm(refused, "apply", debug="1")
    assert r.returncode == 1, r.stderr
    assert "Traceback" in r.stderr, r.stderr
    assert "Refusal: " in r.stderr, r.stderr


def test_a_genuine_bug_still_tracebacks(refused: Path) -> None:
    r = _jm(refused, "status", patch=BUG)
    assert r.returncode == 1, r.stderr
    assert "Traceback" in r.stderr, r.stderr
    assert "ValueError: gh1777 genuine bug" in r.stderr, r.stderr
