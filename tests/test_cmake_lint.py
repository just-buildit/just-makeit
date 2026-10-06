"""
test_cmake_lint.py — cmake-lint clean check for generated CMakeLists.txt files.

Scaffolds a standalone object and a module object, then runs cmake-lint on
every generated CMakeLists.txt.  Formatting rules (C0301 line-length,
C0307 indentation) are delegated to cmake-format and suppressed here so that
the check stays focused on naming conventions (C0103) and correctness (C0113)
— the rules most likely to break downstream cmake-lint users.

cmake-lint is the Makefile's CMAKE_LINT, run through ``tests/_cmakelint.py``
(gh-1930): cmakelang under its pinned Python, never this suite's, and a
crash reported as one rather than as findings. Without CMAKE_LINT -- outside
the make recipe that supplies it -- the tests FAIL; they used to skip.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import _cmakelint

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


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
    def test_no_lint_violations(self, standalone_proj):
        _cmakelint.check(
            standalone_proj.rglob("CMakeLists.txt"), "standalone project"
        )


class TestModuleCmakeLint:
    def test_no_lint_violations(self, module_proj):
        _cmakelint.check(module_proj.rglob("CMakeLists.txt"), "module project")
