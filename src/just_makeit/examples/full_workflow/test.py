"""End-to-end test: full development workflow — tests, coverage, benchmarks, docs.

Demonstrates the complete lifecycle for a just-makeit project:

  1. Scaffold + implement a simple gain component
  2. Build and run CTest + pytest                 (make test)
  3. Run C + Python benchmarks                    (make bench)
  4. Generate C coverage (lcov) + Python coverage (make coverage)
  5. Generate Doxygen + Zensical docs             (make docs)
  6. Verify generated file structure

Steps 4-5 are skipped gracefully when tools are not installed, so the example
passes in minimal CI environments that only have cmake/gcc/python.

Called by tests/test_examples.py as: run(root: Path) -> None
Also runnable directly: python3 examples/full_workflow/test.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


def _cmake_gen():
    return ["-G", "MinGW Makefiles"] if sys.platform == "win32" else []


def _cmd(args, cwd, **kw):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


def _cmake_build(proj: Path) -> None:
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            *_cmake_gen(),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)


def _try_coverage(proj: Path) -> None:
    """Run C (lcov) + Python (pytest-cov) coverage; skip gracefully if missing."""
    if sys.platform == "win32":
        return
    if not shutil.which("lcov") or not shutil.which("genhtml"):
        print("  [full_workflow] skipping C coverage — lcov not found")
        return

    r = subprocess.run(
        [sys.executable, "-c", "import pytest_cov"],
        capture_output=True,
    )
    if r.returncode != 0:
        print("  [full_workflow] skipping Python coverage — pytest-cov not installed")
        return

    _cmd(
        [
            "cmake",
            "-B",
            "build/cov",
            "-S",
            ".",
            *_cmake_gen(),
            "-DCMAKE_BUILD_TYPE=Debug",
            "-DCMAKE_C_FLAGS=--coverage -O0",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build/cov", "--parallel", "4"], cwd=proj)
    _cmd(
        ["ctest", "--test-dir", "build/cov", "--output-on-failure"],
        cwd=proj,
    )

    _cmd(
        [
            "lcov",
            "--capture",
            "--directory",
            "build/cov",
            "--output-file",
            "build/cov/coverage.info",
            "--ignore-errors",
            "inconsistent",
        ],
        cwd=proj,
    )
    _cmd(
        [
            "lcov",
            "--remove",
            "build/cov/coverage.info",
            "/usr/*",
            "*/tests/*",
            "--output-file",
            "build/cov/coverage_filtered.info",
            "--ignore-errors",
            "unused",
        ],
        cwd=proj,
    )
    (proj / "docs" / "coverage" / "c").mkdir(parents=True, exist_ok=True)
    _cmd(
        [
            "genhtml",
            "build/cov/coverage_filtered.info",
            "--output-directory",
            "docs/coverage/c",
        ],
        cwd=proj,
    )
    assert (proj / "docs" / "coverage" / "c" / "index.html").exists(), (
        "genhtml did not produce index.html"
    )

    _cmd(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/",
            "--cov=my_gain",
            "--cov-report=html:docs/coverage/python",
            "--cov-report=term-missing",
        ],
        cwd=proj,
    )
    assert (proj / "docs" / "coverage" / "python" / "index.html").exists(), (
        "pytest-cov did not produce htmlcov index"
    )
    print("  [full_workflow] coverage reports OK")


def _try_docs(proj: Path) -> None:
    """Run doxygen + zensical; skip gracefully if either is not installed."""
    if not shutil.which("doxygen"):
        print("  [full_workflow] skipping docs — doxygen not found")
        return
    _cmd(["doxygen", "Doxyfile"], cwd=proj)
    assert (proj / "docs" / "doxygen" / "html" / "index.html").exists()

    if not shutil.which("zensical"):
        print(
            "  [full_workflow] skipping Python docs — "
            "uv add --dev zensical mkdocstrings-python"
        )
        return
    r = subprocess.run(
        [sys.executable, "-c", "import mkdocstrings"],
        capture_output=True,
    )
    if r.returncode != 0:
        print(
            "  [full_workflow] skipping Python docs — "
            "uv add --dev mkdocstrings-python"
        )
        return
    _cmd(["zensical", "build"], cwd=proj)
    assert (proj / "site" / "index.html").exists(), (
        "zensical build did not produce site/index.html"
    )
    print("  [full_workflow] docs OK")


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new

    # 1. Scaffold: gain(float) -> float, single state var
    jm_new(
        "my_gain",
        root / "my_gain",
        object_names=["gain"],
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    proj = root / "my_gain"

    # 2. Verify scaffolded workflow files exist
    assert (proj / "zensical.toml").exists(), "zensical.toml not generated"
    assert (proj / "docs" / "index.md").exists(), "docs/index.md not generated"
    assert (proj / "docs" / "api.md").exists(), "docs/api.md not generated"
    assert (proj / "Doxyfile").exists(), "Doxyfile not generated"
    assert (proj / "Makefile").exists(), "Makefile not generated"

    zensical_toml = (proj / "zensical.toml").read_text()
    assert "my" in zensical_toml  # site_name contains project slug
    assert "mkdocstrings" in zensical_toml
    assert 'paths = ["src"]' in zensical_toml

    makefile = (proj / "Makefile").read_text()
    assert "coverage" in makefile
    assert "lcov" in makefile
    assert "pytest-cov" in makefile or "--cov=" in makefile
    assert "zensical build" in makefile

    docs_api = (proj / "docs" / "api.md").read_text()
    assert "my_gain" in docs_api

    # 3. Implement gain_step (multiply by gain)
    _cmd(
        [
            sys.executable,
            "-c",
            r"""
import re, pathlib
p = pathlib.Path('native/src/gain/gain_core.c')
src = p.read_text()
src = src.replace(
    'return (float _Complex)0;',
    'return x * state->gain;'
)
p.write_text(src)
""",
        ],
        cwd=proj,
    )

    # 4. Build
    _cmake_build(proj)

    # 5. CTest
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 6. pytest
    _cmd(
        [sys.executable, "-m", "pytest", "src/", "-q"],
        cwd=proj,
    )

    # 7. C benchmarks
    bench_bins = list((proj / "build").glob("bench_*_core"))
    if bench_bins:
        for b in bench_bins:
            _cmd([str(b)], cwd=proj)

    # 8. Python benchmarks (need src/ on path so the built .so is importable)
    import os

    bench_env = {**os.environ, "PYTHONPATH": str(proj / "src")}
    for f in sorted((proj / "src" / "my_gain" / "benchmarks").glob("bench_*.py")):
        _cmd([sys.executable, str(f)], cwd=proj, env=bench_env)

    # 9. Type stub
    pyi = (proj / "src" / "my_gain" / "gain.pyi").read_text()
    assert "class Gain:" in pyi
    assert "gain" in pyi

    # 10. Coverage (skipped if lcov/pytest-cov absent)
    _try_coverage(proj)

    # 11. Docs (skipped if doxygen/zensical absent)
    _try_docs(proj)

    print("full_workflow: all checks passed")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("full_workflow: PASSED")
