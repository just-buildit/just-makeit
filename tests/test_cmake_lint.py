"""
test_cmake_lint.py — cmake-lint clean check for generated CMakeLists.txt files.

Scaffolds a standalone object and a module object, then runs cmake-lint on
every generated CMakeLists.txt.  Formatting rules (C0301 line-length,
C0307 indentation) are delegated to cmake-format and suppressed here so that
the check stays focused on naming conventions (C0103) and correctness (C0113)
— the rules most likely to break downstream cmake-lint users.

Skipped automatically when cmake-lint is not on PATH.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

# Formatting rules are cmake-format's responsibility; suppress them here.
_DISABLED = ["C0301", "C0307"]


def _cmake_lint(cmake_files: list[Path]) -> subprocess.CompletedProcess:
    """Run cmake-lint on *cmake_files*, returning the CompletedProcess."""
    return subprocess.run(
        ["cmake-lint", "--disabled-codes"] + _DISABLED
        + ["--"] + [str(f) for f in cmake_files],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def cmake_lint_bin():
    path = shutil.which("cmake-lint")
    if path is None:
        pytest.skip("cmake-lint not on PATH")
    return path


@pytest.fixture
def standalone_proj(tmp_path):
    """Scaffold a standalone object project."""
    root = tmp_path / "proj"
    new_run("proj", root)
    object_run(root, "engine", None, state_vars=[("gain", "double", "1.0")])
    return root


@pytest.fixture
def module_proj(tmp_path):
    """Scaffold a module object project with a variable_output method."""
    root = tmp_path / "proj"
    new_run("proj", root)
    module_run(root, "dsp")
    object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
    method_run(
        root,
        "nco",
        "steps",
        "dsp",
        arg_type="void",
        return_type="float _Complex",
        variable_output=True,
        multi_output=[],
    )
    return root


class TestStandaloneCmakeLint:
    def test_no_lint_violations(self, cmake_lint_bin, standalone_proj):
        cmake_files = list(standalone_proj.rglob("CMakeLists.txt"))
        assert cmake_files, "no CMakeLists.txt generated"
        r = _cmake_lint(cmake_files)
        assert r.returncode == 0, (
            f"cmake-lint found violations in standalone project:\n{r.stdout}"
        )


class TestModuleCmakeLint:
    def test_no_lint_violations(self, cmake_lint_bin, module_proj):
        cmake_files = list(module_proj.rglob("CMakeLists.txt"))
        assert cmake_files, "no CMakeLists.txt generated"
        r = _cmake_lint(cmake_files)
        assert r.returncode == 0, (
            f"cmake-lint found violations in module project:\n{r.stdout}"
        )
