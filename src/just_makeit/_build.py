"""
_build.py — build, test, and dry-run commands for just-makeit.

These commands operate on an existing project created by `just-makeit new`
(or any project using CMake + just-buildit with the same layout).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import _config as C
from . import _incpath as INC
from . import _testrun
from ._bench import _project_python

# gh-1896. Packaging runs under the PROJECT's interpreter, because
# just_buildit lives in the project's environment, not in jm's own (a
# `uv tool install` has neither it nor numpy). The child prints the wheel's
# path so jm does not have to capture it.
_PACKAGE_SNIPPET = """\
import os, sys
try:
    import just_buildit
except ImportError:
    print("error: just-buildit is not installed in the project's interpreter.",
          file=sys.stderr)
    print("Install it with:  <project python> -m pip install just-buildit",
          file=sys.stderr)
    sys.exit(1)
wheel_dir = sys.argv[1]
name = just_buildit.build_wheel(wheel_dir)
print("just-makeit: " + os.path.join(wheel_dir, name))
"""


def _require(exe: str) -> str:
    path = shutil.which(exe)
    if not path:
        print(f"error: '{exe}' not found on PATH.", file=sys.stderr)
        sys.exit(1)
    return path


def _backend(root: Path) -> str:
    """Return the project's declared build backend, ``cmake`` or ``make``.

    gh-1896. The backend is ``[project] build``, the one answer
    ``C.build_system`` reads. A directory with no manifest (a hand-written
    CMake tree) has no declaration, so it is the default, ``cmake``.
    """
    return C.build_system(C.load(root))


def _make(root: Path, python: str, targets: list[str]) -> None:
    """Run the project's Makefile, pointing its ``PYTHON`` at *python*.

    gh-1896. The make backend's Makefile is the SSOT for how its project is
    built and tested (CLAUDE.md), so jm asks ``make`` rather than
    re-deriving its steps. ``PYTHON=`` on the command line overrides the
    Makefile's ``?=`` default, so the extension and the unittest run under
    the project's own interpreter.
    """
    make = _require("make")
    cmd = [make, f"PYTHON={python}", *targets]
    print(f"just-makeit: {shlex.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        sys.exit(result.returncode)


def _cmake_configure(
    root: Path, build_dir: Path, build_type: str = "Release"
) -> None:
    cmake = _require("cmake")
    python = _project_python(root)
    cmd = [
        cmake,
        "-B",
        str(build_dir),
        "-S",
        str(root),
        f"-DCMAKE_BUILD_TYPE={build_type}",
        f"-DPython3_EXECUTABLE={python}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    print(f"just-makeit: {shlex.join(cmd)}", flush=True)
    # gh-1832. No timeout: a cold build can outrun any fixed budget (603 s
    # measured on a slow board), and the CI job bounds its own wall clock.
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        sys.exit(result.returncode)


def _cmake_build(root: Path, build_dir: Path) -> None:
    cmake = _require("cmake")
    nproc = os.cpu_count() or 4
    cmd = [cmake, "--build", str(build_dir), "--parallel", str(nproc)]
    print(f"just-makeit: {shlex.join(cmd)}", flush=True)
    # gh-1832. Untimed, for the same reason as `_cmake_configure`.
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        sys.exit(result.returncode)


def _ensure_built(root: Path, build_dir: Path) -> None:
    if not (build_dir / "CMakeCache.txt").exists():
        _cmake_configure(root, build_dir)
    _cmake_build(root, build_dir)


def cmd_build(rest: list[str]) -> None:
    """Build the project with its declared backend, then package a wheel.

    gh-1896. The backend is read from ``[project] build``: ``cmake`` runs the
    CMake configure and build, ``make`` runs the Makefile's default target.
    Packaging then calls ``just_buildit.build_wheel`` in the project's
    interpreter, which is the PEP 517 hook the project's own pyproject names.
    """
    root = Path.cwd()
    python = _project_python(root)

    if _backend(root) == "make":
        _make(root, python, [])
    else:
        _ensure_built(root, root / "build")

    wheel_dir = Path(rest[0]) if rest else root / "dist"
    wheel_dir.mkdir(parents=True, exist_ok=True)

    print(f"just-makeit: packaging wheel into {wheel_dir}", flush=True)
    result = subprocess.run(
        [python, "-c", _PACKAGE_SNIPPET, str(wheel_dir)], cwd=str(root)
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


def _has_pytest(python: str) -> bool:
    r = subprocess.run(
        [python, "-c", "import pytest"],
        capture_output=True,
    )
    return r.returncode == 0


def _run_python_tests(root: Path, extra: list[str]) -> bool:
    """Run the project's Python tests under the project's interpreter.

    gh-1896: ``sys.executable`` here was jm's own, so a ``jm test`` run from
    an installed tool ran the project's tests without its numpy or pytest.

    gh-2642: a run that collects nothing FAILS, under pytest and under the
    unittest fallback alike. Every project carries
    ``src/<pkg>/tests/test_project.py`` (gh-2640), so an empty run means the
    tests are gone. gh-1950 once read it as a pass, for a functions-only
    module with no test; gh-2127 gave that module a contract test.
    """
    python = _project_python(root)
    # gh-1832. Untimed, as the `jm-run-tests` precedent leaves its tests.
    if _has_pytest(python):
        args = ["src/", "-v", *extra]
        print(
            f"just-makeit: pytest: {shlex.quote(python)} -m pytest "
            f"{shlex.join(args)}",
            flush=True,
        )
        run = _testrun.pytest(
            [python, "-m"], args, empty_passes=False, cwd=str(root)
        )
        label = "pytest"
    else:
        print(
            f"just-makeit: unittest discover: {shlex.quote(python)} "
            "-m unittest discover -s src/ -p test_*.py -v",
            flush=True,
        )
        run = _testrun.unittest(
            python, "src/", empty_passes=False, cwd=str(root)
        )
        label = "unittest"
    if run.collected_nothing:
        print(
            f"error: {label} ran no tests. Every project has "
            "src/<pkg>/tests/test_project.py, so an empty run means the "
            "tests are gone, and passing it would prove nothing.",
            file=sys.stderr,
            flush=True,
        )
    return run.passed


def run_generated_pytest(proj: Path) -> bool:
    """Run a generated project's OWN pytest suite. True when it passed.

    gh-1089. This is the check that answers "does the project jm generates
    actually pass its own tests" — the artefact a *user* gets, as opposed to
    the walkthrough steps that produced it. It existed in exactly one place,
    ``docker/build_examples.py``, which is not a required check: the image
    build was red on ``main`` for **14 consecutive runs** while every PR gate
    stayed green, and the release was the first thing to stop.

    So the implementation moved here rather than being copied to a second
    caller. ``tests/test_examples.py`` runs it under ``make test-examples``,
    where the other example gates live and where a PR can fail on it.

    Distinct from :func:`_run_python_tests`, which is ``jm test``'s, and the
    two differences are both about being a gate rather than a command:

    * ``PYTHONPATH=src`` — the compiled extension lands in ``src/<pkg>/``, and
      a gate runs against a tree nobody has installed;
    * **no extension, no verdict.** A scaffold that was never built cannot
      import, and failing it would report "the generated tests fail" for a
      project whose tests were never the question.

    Exit 5 (no tests collected) FAILS (gh-2642): a built project always
    carries a test (gh-2640's smoke test, gh-2127's contract test), so a
    suite that collects nothing has lost its tests.

    Returns
    -------
    bool
        True when the suite passed, or there is no built project to test.
    """
    src_dir = proj / "src"
    if not src_dir.is_dir():
        return True
    if not (list(proj.rglob("*.so")) + list(proj.rglob("*.pyd"))):
        return True
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    # gh-1832. Untimed: the gate reads the suite's verdict, not a budget.
    return _testrun.pytest(
        [_project_python(proj), "-m"],
        [str(src_dir), "--tb=short", "-q", "--no-header"],
        empty_passes=False,
        env=env,
        cwd=str(proj),
    ).passed


def cmd_test(rest: list[str]) -> None:
    """Build, then run the project's tests with its declared backend.

    gh-1896. The make backend's ``make test`` owns both the C tests and the
    Python tests, so jm runs only that. It has no slot for pytest arguments,
    so passing any is refused rather than silently dropped. The cmake backend
    runs CTest and then the Python suite, as before.
    """
    root = Path.cwd()

    if _backend(root) == "make":
        if rest:
            print(
                "error: `jm test` passes its arguments to pytest, and the make "
                "backend runs `make test`, which takes none.",
                file=sys.stderr,
            )
            sys.exit(1)
        _make(root, _project_python(root), ["test"])
        return

    build_dir = root / "build"

    _ensure_built(root, build_dir)

    ctest = _require("ctest")
    ctest_cmd = [ctest, "--test-dir", str(build_dir), "--output-on-failure"]
    print(f"just-makeit: {shlex.join(ctest_cmd)}", flush=True)
    r = subprocess.run(ctest_cmd, cwd=str(root))
    ctest_ok = r.returncode == 0

    pytest_ok = _run_python_tests(root, rest)

    if not ctest_ok or not pytest_ok:
        sys.exit(1)


def cmd_dry_run() -> None:
    """Show what would be compiled without building."""
    root = Path.cwd()

    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        print(
            "error: no pyproject.toml found in current directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"just-makeit dry-run  {root}")
    print()

    # C sources
    native = INC.src_root(root)
    if native.is_dir():
        c_files = sorted(native.rglob("*.c"))
        if c_files:
            print("  C sources:")
            for f in c_files:
                print(f"    {f.relative_to(root).as_posix()}")
        else:
            print("  C sources:  (none)")
    else:
        print("  C sources:  native/src/ not found")

    print()

    # Python package
    src = root / "src"
    if src.is_dir():
        py_files = sorted(src.rglob("*.py"))
        pyi_files = sorted(src.rglob("*.pyi"))
        if py_files or pyi_files:
            print("  Python package:")
            for f in py_files + pyi_files:
                print(f"    {f.relative_to(root).as_posix()}")
    print()

    # #2128: name the commands `jm build` would run for this backend, with the
    # same interpreter `jm build` uses, so a make project is not shown a cmake
    # configure it never runs.
    python = _project_python(root)
    if _backend(root) == "make":
        make = shutil.which("make")
        if make:
            cmd = [make, f"PYTHON={python}"]
            print(f"  build:     {shlex.join(cmd)}")
        else:
            print("  build:     make not found")
        print()
        return

    cmake = shutil.which("cmake")
    if cmake:
        build_type = "Release"
        cmd = [
            cmake,
            "-B",
            "build",
            "-S",
            ".",
            f"-DCMAKE_BUILD_TYPE={build_type}",
            f"-DPython3_EXECUTABLE={python}",
        ]
        print(f"  configure: {shlex.join(cmd)}")
        print("  build:     cmake --build build")
    else:
        print("  configure: cmake not found")
    print()
