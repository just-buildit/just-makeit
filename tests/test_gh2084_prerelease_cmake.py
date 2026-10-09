"""gh-2084: a PEP 440 pre-release has a CMake spelling, the release segment.

CMake's `project(VERSION)` takes `major[.minor[.patch[.tweak]]]` integers and
rejects anything else at configure time. gh-1141 found a real project at
`1.1.2a47` whose root CMakeLists could not carry its version, so the drift
gate asked for a value CMake refuses. The decision (gh-2084) is that the CMake
copy carries the release segment, `1.1.2`: the build configures, and the
pre-release stays the manifest and PyPI version.

GATE: a pre-release manifest version, written by `jm config version`, leaves
`status --check` at 0, and the root CMakeLists `project(VERSION)` takes a
spelling CMake accepts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli

PRE = "1.1.2a47"
RELEASE = "1.1.2"

#: The integer grammar CMake's `project(VERSION)` accepts, up to four parts.
CMAKE_VERSION = re.compile(r"^[0-9]+(\.[0-9]+){0,3}$")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert run_cli("new", "vp", cwd=tmp_path).returncode == 0
    root = tmp_path / "vp"
    r = run_cli("config", "version", PRE, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _cmake_project_version(root: Path) -> str:
    text = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    block = re.search(r"^project\s*\([^)]*\)", text, re.I | re.M)
    assert block is not None, text
    m = re.search(r"VERSION\s+([^\s)]+)", block.group(0))
    assert m is not None, text
    return m.group(1)


def test_the_cmake_copy_carries_the_release_segment(project: Path) -> None:
    """The one number CMake can hold: the release, not the pre-release tag."""
    assert _cmake_project_version(project) == RELEASE


def test_the_cmake_spelling_is_one_cmake_accepts(project: Path) -> None:
    assert CMAKE_VERSION.match(_cmake_project_version(project))


def test_status_is_clean_at_a_prerelease(project: Path) -> None:
    """Before the fix the CMake copy stayed at 0.1.0 and `--check` failed on
    the very command that was meant to bump the version."""
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_prerelease_project_configures(
    project: Path, tmp_path: Path
) -> None:
    """The spelling is only worth anything if CMake takes it: configure a
    fresh project at the pre-release and demand exit 0. No skip -- a missing
    cmake fails the gate rather than hiding it, because the build is the
    thing being gated."""
    cmake = shutil.which("cmake")
    assert cmake, "cmake is required: this gate configures, it does not skip"
    r = subprocess.run(
        [cmake, "-S", str(project), "-B", str(tmp_path / "build")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_manifest_keeps_the_pre_release(project: Path) -> None:
    """The spelling is the CMake copy's alone: the manifest and the Python
    copies keep PEP 440 as written."""
    text = (project / "just-makeit.toml").read_text(encoding="utf-8")
    assert f'version = "{PRE}"' in text
    assert f'version = "{PRE}"' in (project / "pyproject.toml").read_text(
        encoding="utf-8"
    )
