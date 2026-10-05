"""gh-1916: install.sh accepts every Python that `requires-python` does.

install.sh was written when jm's floor was 3.11 (5576ba91). 261f86c7
lowered `requires-python` to ``>=3.9`` and brought the README, the docs, CI
and the templates along, but not the installer, which went on refusing 3.9
and 3.10 with ``Python 3.10 found, but 3.11+ is required.`` The docs then
grew a caveat explaining the refusal, which kept it alive.

The installer usually runs from a curl pipe, where no pyproject.toml is
there to read, so it carries the floor itself, once (``PY_FLOOR``). This
file holds that copy to pyproject's by BEHAVIOUR rather than by reading the
literal back: it runs install.sh against an interpreter that reports the
floor and against one that reports the minor below it, both derived from
`requires-python`. A floor change that misses install.sh fails here
whichever way it moves, and so does a check that reads the right number and
compares it wrong.

The interpreter is the suite's own Python with `sys.version_info` replaced
before the installer's ``-c`` probe runs, so no second Python is needed.
``--check`` with a venv path that does not exist stops before any network or
package-manager call, so each run is offline and writes nothing.

GATE: install.sh accepts the Python at pyproject's `requires-python` floor,
      refuses the one below it, and names that floor when it refuses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_stdlib_floor import _floor

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"

pytestmark = pytest.mark.skipif(
    not shutil.which("bash"), reason="install.sh is a bash script"
)

# Replaces what `import sys` hands the installer's `-c` probes, then runs
# the probe. The script is run as `python -c CODE`, so CODE is argv[2].
_SHIM = """\
import sys
sys.version_info = ({major}, {minor}, 0, "final", 0)
exec(sys.argv[2])
"""


def _python_reporting(tmp_path: Path, version: tuple) -> Path:
    """An executable that answers the installer's probes as *version*."""
    major, minor = version
    shim = tmp_path / f"shim_{major}_{minor}.py"
    shim.write_text(_SHIM.format(major=major, minor=minor), encoding="utf-8")
    exe = tmp_path / f"python{major}.{minor}"
    # A shell wrapper rather than a `#!` to sys.executable: a shebang has a
    # length limit, and a CI venv path can exceed it.
    exe.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{shim}" "$@"\n',
        encoding="utf-8",
    )
    exe.chmod(0o755)
    return exe


def _install_check(tmp_path: Path, python: Path):
    """Run ``install.sh --check`` offline against *python*."""
    env = dict(os.environ, PYTHON=str(python))
    return subprocess.run(
        ["bash", str(INSTALL_SH), "--check", str(tmp_path / "no-venv")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _dotted(version: tuple) -> str:
    return ".".join(str(p) for p in version)


def test_the_floor_is_a_major_minor_pair():
    # Everything below compares (major, minor); a `>=3.9.2` floor would
    # need the installer to compare a micro too.
    floor = _floor()
    assert len(floor) == 2 and floor[1] > 0, floor


def test_install_sh_accepts_the_floor(tmp_path):
    floor = _floor()
    res = _install_check(tmp_path, _python_reporting(tmp_path, floor))
    assert "is required" not in res.stderr, (
        f"install.sh refuses Python {_dotted(floor)}, which pyproject's "
        f"requires-python accepts (gh-1916):\n{res.stderr}"
    )
    assert f"Python {_dotted(floor)}" in res.stdout, res.stdout
    # Reaching the --check report proves the run got past every step that
    # could refuse, not merely past the version check.
    assert "Run without --check" in res.stdout or (
        "Everything is up to date" in res.stdout
    ), f"install.sh stopped early:\n{res.stdout}\n{res.stderr}"


def test_install_sh_refuses_below_the_floor_and_names_it(tmp_path):
    floor = _floor()
    below = (floor[0], floor[1] - 1)
    res = _install_check(tmp_path, _python_reporting(tmp_path, below))
    assert res.returncode == 1, (res.stdout, res.stderr)
    want = f"Python {_dotted(below)} found, but {_dotted(floor)}+ is required."
    assert want in res.stderr, res.stderr


def test_install_sh_names_the_floor_when_python_is_missing(tmp_path):
    res = _install_check(tmp_path, tmp_path / "no-such-python")
    assert res.returncode == 1, (res.stdout, res.stderr)
    want = f"Install Python {_dotted(_floor())}+ and re-run."
    assert want in res.stderr, res.stderr
