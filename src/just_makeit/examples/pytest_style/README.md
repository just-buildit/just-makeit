# pytest_style — pure pytest tests and pytest-benchmark

Demonstrates the `--pytest` and `--pytest-benchmark` flags introduced in
just-makeit 0.11.

By default, just-makeit generates test files based on `unittest.TestCase` with
a pytest compatibility shim (so they work under both runners), and standalone
`bench_*.py` scripts that time with `time.perf_counter()`.

Pass `--pytest` to get pure pytest functions instead — no `unittest` import,
no compatibility shim, `pytest.approx` and `pytest.raises` used directly.

Pass `--pytest-benchmark` to get `pytest-benchmark` fixture-style bench files
instead of standalone timing scripts.

Both flags are project-level: set once on `just-makeit new`, inherited
automatically by every subsequent `just-makeit object` call.

______________________________________________________________________

## 1. Scaffold with both flags

```sh
just-makeit new dsp_algo \
    --object dsp_algo \
    --state gain:float:1.0f \
    --pytest \
    --pytest-benchmark
```

Both flags land in `[project]` in `just-makeit.toml`:

```toml
[project]
name = "dsp_algo"
version = "0.1.0"
pytest = "true"
pytest_benchmark = "true"
```

Every subsequent `just-makeit object` call reads these flags and generates the
matching test/bench style automatically — no need to repeat the flags per object.

______________________________________________________________________

## 2. Generated test file (pure pytest)

`src/dsp_algo/tests/test_dsp_algo.py` contains pure pytest functions with no
`unittest` import and no compatibility shim:

```python
import pytest
import numpy as np
from dsp_algo import DspAlgo


def test_create():
    obj = DspAlgo(1.0)
    assert obj is not None

def test_step_runs():
    obj = DspAlgo(1.0)
    y = obj.step(1.0 + 0.0j)
    assert isinstance(y, complex)

def test_steps_shape_dtype():
    obj = DspAlgo(1.0)
    x = np.ones(64, dtype=np.complex64)
    y = obj.steps(x)
    assert y.shape == (64,)
    assert y.dtype == np.complex64

def test_steps_out_param():
    x   = np.ones(64, dtype=np.complex64)
    buf = np.zeros(64, dtype=np.complex64)
    obj1 = DspAlgo(1.0)
    ret = obj1.steps(x, buf)
    assert ret is buf
    obj2 = DspAlgo(1.0)
    np.testing.assert_array_equal(ret, obj2.steps(x))

def test_getter_setter():
    obj = DspAlgo(1.0)
    assert obj.get_gain() == pytest.approx(1.0)
    obj.set_gain(2.0)
    assert obj.get_gain() == pytest.approx(2.0)

def test_reset():
    obj = DspAlgo(1.0)
    obj.set_gain(2.0)
    obj.reset()
    assert obj.get_gain() == pytest.approx(1.0)

def test_context_manager():
    with DspAlgo(1.0) as obj:
        y = obj.step(1.0 + 0.0j)
    assert isinstance(y, complex)

def test_destroy():
    obj = DspAlgo(1.0)
    obj.destroy()
    with pytest.raises(RuntimeError, match="destroyed"):
        obj.step(1.0 + 0.0j)
```

Key differences from the default output:

- No `import unittest` or `class TestDspAlgo(unittest.TestCase)`
- `pytest.approx` instead of the `_approx` shim alias
- `pytest.raises` instead of the `_raises` shim alias
- Plain `assert` — no `self.assertEqual` / `self.assertIs`

______________________________________________________________________

## 3. Generated bench file (pytest-benchmark)

`src/dsp_algo/benchmarks/bench_dsp_algo.py` uses `pytest-benchmark` fixtures
instead of a `time.perf_counter()` loop:

```python
"""Benchmark for DspAlgo.

Run: pytest src/dsp_algo/benchmarks/bench_dsp_algo.py --benchmark-only
"""
import pytest
import numpy as np

from dsp_algo import DspAlgo

BLOCK_1K  = 1_024
BLOCK_64K = 65_536


@pytest.fixture
def obj():
    return DspAlgo(1.0)

def test_bench_step(benchmark, obj):
    benchmark(obj.step, 1.0 + 0.0j)


def test_bench_steps_1k(benchmark, obj):
    x = np.ones(BLOCK_1K, dtype=np.complex64)
    benchmark(obj.steps, x)

def test_bench_steps_64k(benchmark, obj):
    x = np.ones(BLOCK_64K, dtype=np.complex64)
    benchmark(obj.steps, x)
```

Run it with:

```sh
pytest src/dsp_algo/benchmarks/ --benchmark-only
```

Or include benchmarks in a full test run and use `--benchmark-skip` to skip
them by default:

```sh
pytest --benchmark-skip    # normal CI run
pytest --benchmark-only    # benchmark-only run
```
