"""End-to-end test: full development workflow — tests, coverage, benchmarks, docs.

Demonstrates the complete lifecycle for a just-makeit project, including both
benchmark styles side by side:

  1. Scaffold two components:
       - gain    — unittest tests, timeit/perf_counter benchmarks (default style)
       - ema     — pytest tests, pytest-benchmark benchmarks (--pytest-benchmark)
  2. Build and run CTest + both test frameworks         (make test)
  3. Run both benchmark styles                          (make bench)
  4. Generate C coverage (lcov) + Python coverage       (make coverage)
  5. Generate Doxygen + Zensical docs                   (make docs)
  6. Verify generated file structure

Steps 4-5 skip gracefully when tools are not installed, so the example
passes in minimal CI environments that only have cmake/gcc/python.

Called by tests/test_examples.py as: run(root: Path) -> None
Also runnable directly: python3 examples/full_workflow/test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


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
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)


def _try_coverage(proj: Path, package: str) -> None:
    """Run C (lcov) + Python (pytest-cov) coverage; skip gracefully if missing."""
    if not shutil.which("lcov") or not shutil.which("genhtml"):
        print("  [full_workflow] skipping C coverage — lcov not found")
        return

    r = subprocess.run(
        [sys.executable, "-c", "import pytest_cov"],
        capture_output=True,
    )
    if r.returncode != 0:
        print(
            "  [full_workflow] skipping Python coverage — "
            "uv add --dev pytest-cov"
        )
        return

    _cmd(
        [
            "cmake",
            "-B",
            "build/cov",
            "-S",
            ".",
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
    assert (proj / "docs" / "coverage" / "c" / "index.html").exists()

    _cmd(
        [
            sys.executable,
            "-m",
            "pytest",
            "src/",
            f"--cov={package}",
            "--cov-report=html:docs/coverage/python",
            "--cov-report=term-missing",
        ],
        cwd=proj,
    )
    assert (proj / "docs" / "coverage" / "python" / "index.html").exists()
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
    assert (proj / "site" / "index.html").exists()
    print("  [full_workflow] docs OK")


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._init import run as jm_init

    # 1. Scaffold: gain(float→float) with default unittest + timeit benchmarks
    jm_new(
        "my_dsp",
        root / "my_dsp",
        object_names=["gain"],
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        pytest_=False,
        pytest_benchmark_=False,
    )
    proj = root / "my_dsp"

    # 2. Add ema component with pytest + pytest-benchmark style
    jm_init(
        proj,
        "ema",
        state_vars=[("alpha", "float", "0.1f"), ("prev", "float", "0.0f")],
        arg_type="float",
        return_type="float",
        pytest_=True,
        pytest_benchmark_=True,
    )

    # 3. Verify scaffolded workflow files
    assert (proj / "zensical.toml").exists()
    assert (proj / "docs" / "index.md").exists()
    assert (proj / "docs" / "api.md").exists()

    zensical_toml = (proj / "zensical.toml").read_text()
    assert "mkdocstrings" in zensical_toml
    assert 'paths = ["src"]' in zensical_toml

    makefile = (proj / "Makefile").read_text()
    assert "coverage" in makefile
    assert "lcov" in makefile
    assert "--cov=" in makefile
    assert "zensical build" in makefile

    # gain uses timeit bench (standalone python script)
    gain_bench = (
        proj / "src" / "my_dsp" / "benchmarks" / "bench_gain.py"
    ).read_text()
    assert (
        "perf_counter" in gain_bench
        or "timeit" in gain_bench
        or "_bench" in gain_bench
    )
    assert 'if __name__ == "__main__"' in gain_bench

    # ema uses pytest-benchmark (benchmark fixture, no standalone main)
    ema_bench = (
        proj / "src" / "my_dsp" / "benchmarks" / "bench_ema.py"
    ).read_text()
    assert "@pytest.fixture" in ema_bench
    assert "def test_bench_step(benchmark" in ema_bench
    assert 'if __name__ == "__main__"' not in ema_bench

    # gain uses unittest, ema uses pytest
    gain_test = (
        proj / "src" / "my_dsp" / "tests" / "test_gain.py"
    ).read_text()
    assert "import unittest" in gain_test

    ema_test = (proj / "src" / "my_dsp" / "tests" / "test_ema.py").read_text()
    assert "import pytest" in ema_test
    assert "import unittest" not in ema_test

    # 4. Implement gain_step and ema_step
    _cmd(
        [
            sys.executable,
            "-c",
            r"""
import pathlib

p = pathlib.Path('native/src/gain/gain_core.c')
src = p.read_text()
src = src.replace('return (float)0;', 'return x * state->gain;')
p.write_text(src)

p = pathlib.Path('native/src/ema/ema_core.c')
src = p.read_text()
src = src.replace(
    'return (float)0;',
    'float y = state->alpha * x + (1.0f - state->alpha) * state->prev;\n'
    '    state->prev = y;\n'
    '    return y;'
)
p.write_text(src)
""",
        ],
        cwd=proj,
    )

    # 5. Build
    _cmake_build(proj)

    # 6. CTest
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 7. pytest (ema tests) — exit code 5 means no tests collected, also OK
    _has_pytest = (
        subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True,
        ).returncode
        == 0
    )
    if _has_pytest:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "src/", "-q"],
            cwd=proj,
            capture_output=True,
            text=True,
        )
        if r.returncode not in (0, 5):
            raise AssertionError(
                f"pytest failed:\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
            )
    else:
        print(
            "  [full_workflow] skipping pytest run — "
            "install with: pip install pytest"
        )

    # 8. unittest (gain tests only — ema uses pytest which may not be installed)
    _cmd(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "src",
            "-p",
            "test_gain.py",
            "-v",
        ],
        cwd=proj,
    )

    # 9. C benchmarks
    for b in sorted((proj / "build").glob("bench_*_core")):
        _cmd([str(b)], cwd=proj)

    # 10. timeit-style Python benchmarks (gain — standalone runnable)
    bench_env = {**os.environ, "PYTHONPATH": str(proj / "src")}
    gain_bench_path = proj / "src" / "my_dsp" / "benchmarks" / "bench_gain.py"
    if gain_bench_path.exists():
        _cmd([sys.executable, str(gain_bench_path)], cwd=proj, env=bench_env)

    # 11. pytest-benchmark benchmarks (ema — needs --benchmark-only)
    r = subprocess.run(
        [sys.executable, "-c", "import pytest_benchmark"],
        capture_output=True,
    )
    if r.returncode == 0:
        _cmd(
            [
                sys.executable,
                "-m",
                "pytest",
                "src/my_dsp/benchmarks/bench_ema.py",
                "--benchmark-only",
                "-v",
            ],
            cwd=proj,
            env=bench_env,
        )
    else:
        print(
            "  [full_workflow] skipping pytest-benchmark run — "
            "uv add --dev pytest-benchmark"
        )

    # 12. Coverage (skipped if lcov/pytest-cov absent)
    _try_coverage(proj, "my_dsp")

    # 13. Docs (skipped if doxygen/zensical absent)
    _try_docs(proj)

    print("full_workflow: all checks passed")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("full_workflow: PASSED")
