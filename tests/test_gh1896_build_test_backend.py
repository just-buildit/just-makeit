"""gh-1896, gh-1950, gh-1832: `jm build` / `jm test` drive the project.

Three properties of ``_build``, each reproduced on ``main`` before the fix:

* **gh-1896** -- the backend is read from ``[project] build``. A make-backend
  project has no ``CMakeLists.txt``, so ``jm build`` and ``jm test`` used to
  fail configuring CMake against an empty directory. Both now hand the build
  and the test to the project's own Makefile, and the packaging and the
  Python tests run under the project's interpreter (``.venv`` when present),
  not jm's own.
* **gh-1950** -- a project of only module functions collects no pytest tests,
  and pytest's exit 5 for that is a shape, not a failure. ``jm test`` read it
  as a failure, where ``run_generated_pytest`` already read it as a pass.
* **gh-1832** -- no fixed budget on any subprocess ``jm build`` / ``jm test``
  starts. A cold build on a slow board took 603 s against a 600 s literal.

Every test drives jm through ``run_cli`` and builds a real project. The
subprocess spy below records each call and lets it run; it asserts on the
commands jm actually issued, not on a mock's return value.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402


@pytest.fixture
def spy(monkeypatch):
    """Record every ``subprocess.run`` jm makes, and let it run for real."""
    real_run = subprocess.run
    calls: list[tuple[list[str], dict]] = []

    def recording_run(cmd, *args, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    return calls


def _make_project(tmp_path: Path, name: str, *args: str) -> Path:
    """Scaffold *name* under *tmp_path* with ``just-makeit new``."""
    r = run_cli("new", name, *args, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    # The wheel's repair step (auditwheel / delvewheel) is not under test here,
    # and a CI image without patchelf dies in it. Turning it off keeps these
    # tests about the build backend, and makes them run on any box.
    proj = tmp_path / name
    pp = proj / "pyproject.toml"
    body = pp.read_text(encoding="utf-8")
    old_tbl = 'command = "make just-build"\n'
    assert body.count(old_tbl) == 1, "scaffold's [tool.just-buildit] changed"
    pp.write_text(
        body.replace(old_tbl, old_tbl + "repair = false\n"), encoding="utf-8"
    )
    return proj


def _functions_only_module(tmp_path: Path) -> Path:
    """The gh-1950 shape: a module with one function and no object."""
    proj = _make_project(tmp_path, "q", "--module", "m")
    r = run_cli(
        "function",
        "f",
        "--module",
        "m",
        "--param",
        "x:float",
        "--return-type",
        "float",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    return proj


def test_make_backend_build_runs_the_makefile_not_cmake(tmp_path, spy):
    """gh-1896: `jm build` on a make project builds it and never calls CMake.

    On main this exits 1 with ``The source directory ... does not appear to
    contain CMakeLists.txt``. The Makefile owns the build, and the wheel
    comes from the project's own PEP 517 hook.
    """
    if shutil.which("make") is None:
        pytest.skip("needs make on PATH")
    proj = _make_project(
        tmp_path, "mk", "--object", "g", "--build-system", "make"
    )

    r = run_cli("build", cwd=proj)

    assert r.returncode == 0, r.stderr
    assert not any(cmd[0].endswith("cmake") for cmd, _ in spy), spy
    assert any(cmd[0].endswith("make") for cmd, _ in spy), spy
    assert list((proj / "dist").glob("*.whl")), "no wheel was written"


def test_make_backend_test_runs_make_test(tmp_path, spy):
    """gh-1896: `jm test` on a make project is `make test`, and only that."""
    if shutil.which("make") is None:
        pytest.skip("needs make on PATH")
    proj = _make_project(
        tmp_path, "mk", "--object", "g", "--build-system", "make"
    )

    r = run_cli("test", cwd=proj)

    assert r.returncode == 0, r.stderr
    make_calls = [cmd for cmd, _ in spy if cmd[0].endswith("make")]
    assert make_calls and make_calls[-1][-1] == "test", make_calls
    assert not any(cmd[0].endswith("ctest") for cmd, _ in spy), spy


def _add_project_venv(proj: Path) -> Path:
    """Give *proj* a ``.venv`` whose interpreter is the test's own, and return
    its path.

    A wrapper, not a symlink: a symlink run from here loses its real venv's
    site-packages (python finds the venv from the path it was launched by),
    so numpy would look absent and the Makefile would try to pip install.
    """
    venv_bin = proj / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    interp = venv_bin / "python"
    interp.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    interp.chmod(0o755)
    return interp


def test_make_backend_uses_the_project_interpreter(tmp_path, spy):
    """gh-1896: the project's ``.venv`` is the interpreter, not jm's.

    The make scaffold's ``PYTHON ?=`` default is ``python3`` from PATH, which
    is not the environment the project's numpy and just-buildit live in. The
    make command gets ``PYTHON=`` for the build and the test, and the wheel
    is packaged by the same interpreter.
    """
    if shutil.which("make") is None:
        pytest.skip("needs make on PATH")
    proj = _make_project(
        tmp_path, "mk", "--object", "g", "--build-system", "make"
    )
    interp = _add_project_venv(proj)

    r = run_cli("build", cwd=proj)
    assert r.returncode == 0, r.stderr
    make_calls = [cmd for cmd, _ in spy if cmd[0].endswith("make")]
    assert f"PYTHON={interp}" in make_calls[0], make_calls
    packaging = [
        cmd for cmd, _ in spy if "-c" in cmd and "build_wheel" in cmd[-2]
    ]
    assert packaging and packaging[0][0] == str(interp), packaging

    r = run_cli("test", cwd=proj)
    assert r.returncode == 0, r.stderr
    make_calls = [cmd for cmd, _ in spy if cmd[0].endswith("make")]
    assert f"PYTHON={interp}" in make_calls[-1], make_calls


def test_cmake_backend_configures_against_the_project_interpreter(
    tmp_path, spy
):
    """gh-1896: CMake's ``Python3_EXECUTABLE`` is the project's ``.venv``.

    Before the fix this was jm's ``sys.executable``, so a numpy-less jm tool
    environment configured an extension against an interpreter with no numpy.
    """
    proj = _make_project(tmp_path, "cm", "--object", "g")
    interp = _add_project_venv(proj)

    r = run_cli("build", cwd=proj)

    assert r.returncode == 0, r.stderr
    configure = [
        cmd for cmd, _ in spy if "-DPython3_EXECUTABLE=" in " ".join(cmd)
    ]
    assert configure, spy
    assert f"-DPython3_EXECUTABLE={interp}" in configure[0], configure


def test_make_backend_refuses_pytest_arguments(tmp_path, spy):
    """gh-1896: ``jm test -k x`` on a make project is refused, not dropped.

    ``make test`` takes no arguments, so passing a pytest selector would
    silently run everything. The refusal names the reason.
    """
    if shutil.which("make") is None:
        pytest.skip("needs make on PATH")
    proj = _make_project(
        tmp_path, "mk", "--object", "g", "--build-system", "make"
    )

    r = run_cli("test", "-k", "g", cwd=proj)

    assert r.returncode == 1
    assert "make" in r.stderr and "takes none" in r.stderr
    assert not spy, "a refused test must not start any build"


def test_functions_only_module_passes_jm_test(tmp_path, spy):
    """gh-1950: pytest's "no tests collected" (exit 5) is a pass.

    The issue's repro: `jm test` on a project of only module functions exits
    1 with ``collected 0 items``. The C tests pass and the Python suite has
    nothing to collect, which is the same verdict ``run_generated_pytest``
    gives this shape.
    """
    proj = _functions_only_module(tmp_path)

    r = run_cli("test", cwd=proj)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "collected no tests" in r.stdout


def test_no_subprocess_jm_starts_carries_a_timeout(tmp_path, spy):
    """gh-1832: no ``timeout=`` on any subprocess that ``jm test`` starts.

    Covers configure, build, ctest, the pytest import probe and the pytest
    run itself, because they all go through ``subprocess.run`` in the same
    process. The project is the functions-only module, whose build is the
    cheapest real one with every step in it.
    """
    proj = _functions_only_module(tmp_path)

    r = run_cli("test", cwd=proj)

    assert r.returncode == 0, r.stdout + r.stderr
    assert spy, "the spy saw no subprocess; the gate is not armed"
    timed = [cmd for cmd, kwargs in spy if "timeout" in kwargs]
    assert not timed, timed


def test_darwin_links_the_extension_with_dynamic_lookup(tmp_path):
    """gh-2169: a Mach-O bundle must leave the interpreter's symbols to load
    time, or the link fails on macOS with _PyCapsule_* undefined. Checked as a
    dry run with the platform forced, so it runs on every box: the flag is on
    the Darwin link line and absent from the Linux one."""
    proj = _make_project(
        tmp_path, "q", "--object", "g", "--build-system", "make"
    )
    darwin = subprocess.run(
        ["make", "-n", "UNAME_S=Darwin"],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    linux = subprocess.run(
        ["make", "-n", "UNAME_S=Linux"],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    assert darwin.returncode == 0, darwin.stderr
    assert "-undefined dynamic_lookup" in darwin.stdout, darwin.stdout
    assert "-undefined dynamic_lookup" not in linux.stdout, linux.stdout
