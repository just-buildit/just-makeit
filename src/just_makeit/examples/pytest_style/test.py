"""End-to-end test: --pytest and --pytest-benchmark scaffold verification.

Verifies that scaffolding with both flags produces:
  - Pure pytest test files (no unittest, no shim, pytest.approx/raises)
  - pytest-benchmark bench files (@pytest.fixture, benchmark fixture param)
  - TOML correctly records both flags
  - Second object inherits flags from project config
  - jm script round-trip preserves both flags
  - jm add regeneration keeps the chosen framework

This test does not build any C code — it only inspects generated file content.
"""

import tempfile
from pathlib import Path

HERE = Path(__file__).parent


def run(root: Path) -> None:
    from just_makeit._new import run as jm_new
    from just_makeit._init import run as jm_init
    from just_makeit._add import run as jm_add
    from just_makeit._config import load, is_pytest, is_pytest_benchmark

    # 1. Scaffold with both flags
    proj = root / "dsp_algo"
    jm_new(
        "dsp_algo",
        proj,
        object_names=["dsp_algo"],
        state_vars=[("gain", "float", "1.0f")],
        pytest_=True,
        pytest_benchmark_=True,
    )

    # 2. TOML records both flags
    cfg = load(proj)
    assert is_pytest(cfg), "is_pytest() should be True"
    assert is_pytest_benchmark(cfg), "is_pytest_benchmark() should be True"
    toml_text = (proj / "just-makeit.toml").read_text(encoding="utf-8")
    assert 'pytest = "true"' in toml_text
    assert 'pytest_benchmark = "true"' in toml_text

    # 3. Test file is pure pytest (no unittest shim)
    test_py = (
        proj / "src" / "dsp_algo" / "tests" / "test_dsp_algo.py"
    ).read_text(encoding="utf-8")
    assert "import unittest" not in test_py, "no unittest import"
    assert "import pytest" in test_py, "must import pytest"
    assert "compatibility shim" not in test_py, "no shim comment"
    assert "_approx" not in test_py, "no _approx alias"
    assert "_raises" not in test_py, "no _raises alias"
    assert "class TestDspAlgo" not in test_py, "no unittest class"
    assert "def test_create():" in test_py, "top-level test_create"
    assert "def test_getter_setter():" in test_py
    assert "def test_reset():" in test_py
    assert "pytest.approx" in test_py, "uses pytest.approx"
    assert "pytest.raises" in test_py, "uses pytest.raises"
    assert "self.assert" not in test_py, "no unittest-style asserts"

    # 4. Bench file uses pytest-benchmark
    bench_py = (
        proj / "src" / "dsp_algo" / "benchmarks" / "bench_dsp_algo.py"
    ).read_text(encoding="utf-8")
    assert "perf_counter" not in bench_py, "no perf_counter"
    assert "import pytest" in bench_py
    assert "@pytest.fixture" in bench_py
    assert "def obj():" in bench_py
    assert "def test_bench_step(benchmark, obj):" in bench_py
    assert "benchmark(obj.step," in bench_py
    assert "def test_bench_steps_1k(benchmark, obj):" in bench_py
    assert "def test_bench_steps_64k(benchmark, obj):" in bench_py
    assert "def main()" not in bench_py, "no standalone main"

    # 5. Second object inherits flags from project config (no flags needed)
    jm_init(proj, "filter2", [("bw", "double", "0.1")])
    test2 = (
        proj / "src" / "dsp_algo" / "tests" / "test_filter2.py"
    ).read_text(encoding="utf-8")
    assert "import unittest" not in test2, "inherited: no unittest"
    assert "import pytest" in test2, "inherited: uses pytest"
    bench2 = (
        proj / "src" / "dsp_algo" / "benchmarks" / "bench_filter2.py"
    ).read_text(encoding="utf-8")
    assert "perf_counter" not in bench2, "inherited: no perf_counter"
    assert "@pytest.fixture" in bench2, "inherited: pytest-benchmark"

    # 6. jm add regeneration preserves pure-pytest style
    jm_add(proj, "dsp_algo", [("order", "int", "4")], force=True)
    test_after_add = (
        proj / "src" / "dsp_algo" / "tests" / "test_dsp_algo.py"
    ).read_text(encoding="utf-8")
    assert "import unittest" not in test_after_add, "add: still pure pytest"
    assert "get_order" in test_after_add, "add: new var appears in test"
    bench_after_add = (
        proj / "src" / "dsp_algo" / "benchmarks" / "bench_dsp_algo.py"
    ).read_text(encoding="utf-8")
    assert "perf_counter" not in bench_after_add, "add: still pytest-benchmark"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("pytest_style: PASSED")
