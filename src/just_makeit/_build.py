"""
_build.py — build, test, and dry-run commands for just-makeit.

These commands operate on an existing project created by `just-makeit init`
(or any project using CMake + just-buildit with the same layout).
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def _require(exe: str) -> str:
    path = shutil.which(exe)
    if not path:
        print(f"error: '{exe}' not found on PATH.", file=sys.stderr)
        sys.exit(1)
    return path


def _cmake_configure(root: Path, build_dir: Path, build_type: str = "Release") -> None:
    cmake = _require("cmake")
    python = sys.executable
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
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        sys.exit(result.returncode)


def _cmake_build(root: Path, build_dir: Path) -> None:
    cmake = _require("cmake")
    nproc = os.cpu_count() or 4
    cmd = [cmake, "--build", str(build_dir), "--parallel", str(nproc)]
    print(f"just-makeit: {shlex.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        sys.exit(result.returncode)


def _ensure_built(root: Path, build_dir: Path) -> None:
    if not (build_dir / "CMakeCache.txt").exists():
        _cmake_configure(root, build_dir)
    _cmake_build(root, build_dir)


def cmd_build(rest: list[str]) -> None:
    """Configure + build C extension, then package a wheel via just-buildit."""
    root = Path.cwd()
    build_dir = root / "build"

    _ensure_built(root, build_dir)

    wheel_dir = Path(rest[0]) if rest else root / "dist"
    wheel_dir.mkdir(parents=True, exist_ok=True)

    try:
        import just_buildit
    except ImportError:
        print("error: just-buildit is not installed.", file=sys.stderr)
        print("Install it with:  pip install just-buildit", file=sys.stderr)
        sys.exit(1)

    print(f"just-makeit: packaging wheel into {wheel_dir}", flush=True)
    name = just_buildit.build_wheel(str(wheel_dir))
    print(f"just-makeit: {wheel_dir / name}")


def _has_pytest() -> bool:
    r = subprocess.run(
        [sys.executable, "-c", "import pytest"],
        capture_output=True,
    )
    return r.returncode == 0


def _run_python_tests(root: Path, extra: list[str]) -> bool:
    if _has_pytest():
        cmd = [sys.executable, "-m", "pytest", "src/", "-v", *extra]
        label = "pytest"
    else:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "src/", "-v"]
        label = "unittest discover"

    print(f"just-makeit: {label}: {shlex.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=str(root)).returncode == 0


def cmd_test(rest: list[str]) -> None:
    """Build, then run CTest + pytest."""
    root = Path.cwd()
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
        print("error: no pyproject.toml found in current directory.", file=sys.stderr)
        sys.exit(1)

    print(f"just-makeit dry-run  {root}")
    print()

    # C sources
    native = root / "native" / "src"
    if native.is_dir():
        c_files = sorted(native.rglob("*.c"))
        if c_files:
            print("  C sources:")
            for f in c_files:
                print(f"    {f.relative_to(root)}")
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
                print(f"    {f.relative_to(root)}")
    print()

    cmake = shutil.which("cmake")
    if cmake:
        build_type = "Release"
        python = sys.executable
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
