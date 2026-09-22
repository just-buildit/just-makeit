"""gh-1376: a generated project builds and tests through its CMakePresets.json.

`test_gh1376_cmake_presets.py` holds the presets to the Makefile's configure
line; this drives them the way an IDE does -- `cmake --preset`,
`cmake --build --preset`, `ctest --preset` -- with `CC` and `CMAKE_GENERATOR`
REMOVED from the environment, so the preset alone must select the compiler,
the generator and the interpreter. Then the built extension is imported by the
venv's own interpreter.

It picks the presets for the host it runs on, which is the point: the Windows
job (`Examples (windows-latest, clang-cl)`) runs this file too, and there it
exercises the `windows-*` presets Visual Studio shows. Debug as well as
Release, because Debug is the configuration whose C runtime once broke the
clang-cl link (gh-1368).

The preset names the project's `.venv`, so the test makes one. A venv created
from inside the test runner's venv does not see the runner's packages
(`--system-site-packages` reaches the BASE interpreter), so a `.pth` file
points it at the directory numpy is installed in.

Lives on PROJECT_ENV_TESTS: it needs cmake, Ninja and a C compiler.

GATE: a fresh project configures, builds, tests and imports through
      `cmake --preset` on the host's own presets, with nothing in the
      environment choosing for it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy
import pytest

from _jmrun import run_cli

_WINDOWS = sys.platform == "win32"
_PRESETS = (
    ("windows-release", "windows-debug") if _WINDOWS else ("release", "debug")
)


def _run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    assert r.returncode == 0, (
        f"{cmd} exited {r.returncode}\n{r.stdout}\n{r.stderr}"
    )
    return r


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("presets_build")
    for args in (("new", "presets_probe"), ("object", "gain")):
        cwd = root if args[0] == "new" else root / "presets_probe"
        r = run_cli(*args, cwd=cwd)
        assert r.returncode == 0, r.stderr
    proj = root / "presets_probe"

    # The interpreter the preset names, created where it names it.
    _run([sys.executable, "-m", "venv", "--without-pip", ".venv"], proj)
    py = proj / ".venv" / ("Scripts/python.exe" if _WINDOWS else "bin/python")
    purelib = _run(
        [
            str(py),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        proj,
    ).stdout.strip()
    numpy_home = Path(numpy.__file__).resolve().parent.parent
    (Path(purelib) / "jm_numpy.pth").write_text(f"{numpy_home}\n")
    return proj


@pytest.mark.parametrize("preset", _PRESETS)
def test_the_preset_configures_builds_and_tests(project, preset):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CC", "CMAKE_GENERATOR")
    }
    _run(["cmake", "--preset", preset], project, env)
    _run(["cmake", "--build", "--preset", preset], project, env)
    _run(["ctest", "--preset", preset], project, env)

    cache = (project / "out" / "build" / preset / "CMakeCache.txt").read_text()
    assert ".venv" in cache.split("Python3_EXECUTABLE:")[1].splitlines()[0]
    assert not (project / "build").exists(), "the preset wrote make's build/"

    py = (
        project
        / ".venv"
        / ("Scripts/python.exe" if _WINDOWS else "bin/python")
    )
    _run(
        [
            str(py),
            "-c",
            "import sys; sys.path.insert(0, 'src');"
            " from presets_probe import Gain; Gain()",
        ],
        project,
    )
