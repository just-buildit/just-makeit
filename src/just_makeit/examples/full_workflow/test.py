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

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from just_makeit._bench import child_pytest_env

HERE = Path(__file__).parent

# Hand-authored one-sentence class summaries. The sacred `<obj>_core.h` header
# is the single source of truth for docs: `jm` turns the `@brief` on
# `<obj>_create()` into the generated class's docstring summary (state `@param`
# entries stay generic and need no enrichment). This practices exactly what the
# README's "Doxygen (C API)" section preaches — add `/** @brief ... */` and it
# flows straight through to the rendered docs. A follow-up `jm apply`
# regenerates the `.pyi` from these edited comments.
CLASS_SUMMARIES = {
    "gain": "Scale each input sample by a constant gain factor.",
    "ema": "Exponentially weighted moving average — a one-pole "
    "low-pass smoother.",
}


def _enrich_headers(proj: Path) -> None:
    """Replace jm's scaffold `@brief` on each `<obj>_create` with a real one."""
    for obj, summary in CLASS_SUMMARIES.items():
        header = proj / "native" / "inc" / obj / f"{obj}_core.h"
        text = header.read_text(encoding="utf-8")
        scaffold_re = re.compile(
            rf"/\*\*\n \* @brief Create a {obj} instance\..*?"
            rf"(?={obj}_state_t \*{obj}_create)",
            re.DOTALL,
        )
        text, n = scaffold_re.subn(
            f"/**\n * @brief {summary}\n */\n", text, count=1
        )
        assert n == 1, f"{obj}_create scaffold brief not found"
        header.write_text(text, encoding="utf-8")


def _cmd(args, cwd, **kw):
    # Every command here runs against the *scaffolded* project, so none of them
    # should inherit the outer suite's pytest state. The same helper `jm bench`
    # uses -- see its docstring for why an inherited PYTEST_XDIST_WORKER makes
    # `pytest --benchmark-only` fail outright.
    kw.setdefault("env", child_pytest_env())
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=600, **kw
    )
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
        timeout=600,
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
        timeout=600,
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
    from just_makeit._apply import run as apply_run
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

    # 4b. Enrich the sacred headers with real Doxygen, then regenerate the
    # glue. The hand-written `@brief` on each `<obj>_create` becomes the
    # generated class's docstring summary; `jm apply` re-derives the `.pyi`
    # (and other glue) from the edited comments without touching the `.c`
    # implementations patched above.
    _enrich_headers(proj)
    apply_run(proj)

    gain_pyi = (proj / "src" / "my_dsp" / "gain.pyi").read_text()
    ema_pyi = (proj / "src" / "my_dsp" / "ema.pyi").read_text()
    assert CLASS_SUMMARIES["gain"] in gain_pyi, "gain class @brief missing"
    assert CLASS_SUMMARIES["ema"] in ema_pyi, "ema class @brief missing"

    # 5. Build
    _cmake_build(proj)

    # 6. CTest
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 7. pytest (ema tests) — exit code 5 means no tests collected, also OK
    _has_pytest = (
        subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True,
            timeout=600,
        ).returncode
        == 0
    )
    if _has_pytest:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "src/", "-q"],
            cwd=proj,
            capture_output=True,
            text=True,
            timeout=600,
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
    # child_pytest_env(), not os.environ: this dict is passed explicitly, so it
    # would otherwise re-import the very PYTEST_XDIST_WORKER that _cmd strips.
    bench_env = {**child_pytest_env(), "PYTHONPATH": str(proj / "src")}
    gain_bench_path = proj / "src" / "my_dsp" / "benchmarks" / "bench_gain.py"
    if gain_bench_path.exists():
        _cmd([sys.executable, str(gain_bench_path)], cwd=proj, env=bench_env)

    # 11. pytest-benchmark benchmarks (ema — needs --benchmark-only)
    r = subprocess.run(
        [sys.executable, "-c", "import pytest_benchmark"],
        capture_output=True,
        timeout=600,
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
