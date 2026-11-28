"""gh-2857: the cmake configure a test RUNS names its interpreter.

The static scan (`test_gh2857_cmake_names_python`) learns spellings one at
a time, and the review of #2871 found three it still let through. So the
argv is checked as it runs: `tests/conftest.py` wraps ``subprocess.Popen``
for every test (`_cmake.unnamed_configure`). These run the reviewer's three
spellings against a real scaffolded project, each with no interpreter, and
each is stopped -- and the same configure naming one, and ``cmake
--build``, go through.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _cmake import PYTHON3_ARG
from _compilers import default_cc
from _jmrun import run_cli

pytestmark = pytest.mark.skipif(
    not shutil.which("cmake"), reason="cmake not found"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "p"


def _stopped(call) -> str:
    with pytest.raises(pytest.fail.Exception) as e:
        call()
    msg = str(e.value)
    assert "names no interpreter" in msg and "gh-2857" in msg, msg
    return msg


def test_a_which_value_in_a_variable_is_stopped(project):
    exe = shutil.which("cmake")
    _stopped(
        lambda: subprocess.run(
            [exe, "-S", str(project), "-B", str(project / "b")],
            capture_output=True,
        )
    )


def test_a_helpers_forwarded_args_are_stopped(project):
    args = ["-S", str(project), "-B", str(project / "b")]
    _stopped(lambda: subprocess.run(["cmake", *args], capture_output=True))


def test_a_positional_source_is_stopped(project):
    build = project / "b"
    build.mkdir()
    _stopped(
        lambda: subprocess.check_output(["cmake", str(project)], cwd=build)
    )


def test_a_shell_string_is_stopped(project):
    _stopped(
        lambda: subprocess.run(
            f"cmake -S {project} -B {project / 'b'}",
            shell=True,
            capture_output=True,
        )
    )


def test_a_named_configure_and_a_build_go_through(project):
    build = project / "b"
    r = subprocess.run(
        ["cmake", "-S", str(project), "-B", str(build), PYTHON3_ARG],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    r = subprocess.run(
        ["cmake", "--build", str(build), "-j", "4"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


_C_ONLY = (
    "cmake_minimum_required(VERSION 3.16)\n"
    "project(smoke C)\n"
    "add_executable(smoke smoke.c)\n"
)


def test_a_c_only_consumer_has_no_interpreter_to_name(tmp_path):
    """A C project (fir_filter's installed-library consumer) builds no
    extension: its configure goes through with no interpreter named, and
    naming one would be a false claim. Read from its CMake, not a list."""
    if default_cc() is None:
        pytest.skip("no C compiler found")
    src = tmp_path / "consumer"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(_C_ONLY, encoding="utf-8")
    (src / "smoke.c").write_text(
        "int main(void) { return 0; }\n", encoding="utf-8"
    )
    r = subprocess.run(
        ["cmake", "-B", "build", "-S", "."],
        cwd=src,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def test_python_in_an_included_cmake_file_still_counts(tmp_path):
    # The guard reads every CMakeLists.txt and .cmake file in the source
    # tree: a project that asks for Python3 from an include is guarded.
    src = tmp_path / "proj"
    (src / "cmake").mkdir(parents=True)
    (src / "CMakeLists.txt").write_text(
        _C_ONLY + "include(cmake/py.cmake)\n", encoding="utf-8"
    )
    (src / "cmake" / "py.cmake").write_text(
        "find_package(Python3 REQUIRED COMPONENTS Development)\n",
        encoding="utf-8",
    )
    _stopped(
        lambda: subprocess.run(
            ["cmake", "-B", "build", "-S", "."], cwd=src, capture_output=True
        )
    )
