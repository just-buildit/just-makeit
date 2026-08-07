# Testing & benchmarking

## Extending an object's state

```sh
just-makeit add --object gain --state drive:double:1.0
```

Adding state is **structural**. `add` writes the new `[[gain.state]]` entry to
`just-makeit.toml`, then rebuilds the object from the manifest via the
regenerate path (delete + apply) — the new field reaches the struct, the
constructor, the getter/setter, and reset in one shot. The rebuild **discards
hand-written `_core.c` bodies and the inline `step()` body in `_core.h`**,
so keep your algorithm in the TOML `impl`/`create_impl` (the rebuild
re-asserts it) or `git stash` first. `add` prompts for one confirmation before
rebuilding; `--force` skips it. When the project has a single standalone
object, `--object` may be omitted.

______________________________________________________________________

## Benchmarking

```sh
make bench    # C timing loop + Python perf_counter suite
```

The C benchmark in `native/benchmarks/bench_gain_core.c` runs a raw timing
loop — useful for measuring SIMD uplift without Python overhead. `make bench`
works on both build backends (gh-832; the `--build-system make` backend gained
its `bench:` target and `C_BENCHES` list there).

jm can only auto-populate the timing loop for a shape it can size: a `step()`,
or a method that is not `variable_output` / `out_type` / `varargs` / `codec`.
For anything else the file is a **scaffold with a `TODO:`** naming the
candidate methods and showing a worked `jm_bench_add` call to copy — fill it in
and the target measures. `jm status` lists the unfilled ones under `SILENT`. The Python
benchmark script runs as a plain script (`python bench_gain.py`) and reports
ns/call for `step()` and µs + MSa/s for `steps()`.

______________________________________________________________________

## Generated tests and benchmarks

Every object also gets a Python test file and a benchmark file, placed in
`tests/` and `benchmarks/` directories next to the package. Both are ready
to run immediately after `pip install .`.

For the same `Gain` example, `src/my_dsp/tests/test_gain.py` contains:

```python
import unittest
import numpy as np
from my_dsp import Gain

# pytest compatibility shim (runs under pytest or plain unittest discover)
...

class TestGain(unittest.TestCase):
    def test_create(self):
        obj = Gain(1.0)
        self.assertIsNotNone(obj)

    def test_step_runs(self):
        obj = Gain(1.0)
        y = obj.step(1.0)
        assert isinstance(y, float)

    def test_steps_shape_dtype(self):
        obj = Gain(1.0)
        x = np.ones(64, dtype=np.float32)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, np.float32)

    def test_steps_out_param(self):
        x   = np.ones(64, dtype=np.float32)
        buf = np.zeros(64, dtype=np.float32)
        obj1 = Gain(1.0)
        ret = obj1.steps(x, buf)
        self.assertIs(ret, buf)

    def test_getter_setter(self):
        obj = Gain(1.0)
        assert obj.get_gain() == _approx(1.0)
        obj.set_gain(2.0)
        assert obj.get_gain() == _approx(2.0)

    def test_reset(self):
        obj = Gain(1.0)
        obj.set_gain(2.0)
        obj.reset()
        assert obj.get_gain() == _approx(1.0)

    def test_context_manager(self):
        with Gain(1.0) as obj:
            y = obj.step(1.0)
        assert isinstance(y, float)

    def test_destroy(self):
        obj = Gain(1.0)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj.step(1.0)
```

And `src/my_dsp/benchmarks/bench_gain.py`:

```python
"""Benchmark for Gain.

Run standalone:  python src/my_dsp/benchmarks/bench_gain.py
Or via make:     make bench
"""
import time
import numpy as np
from my_dsp import Gain

REPS      = 1_000
BLOCK_1K  = 1_024
BLOCK_64K = 65_536


def _bench(label: str, fn, *args, reps: int = REPS) -> float:
    for _ in range(max(1, reps // 10)):  # warmup
        fn(*args)
    t0 = time.perf_counter()
    for _ in range(reps):
        fn(*args)
    return (time.perf_counter() - t0) / reps


def main() -> None:
    obj = Gain(1.0)
    print("gain")
    dt = _bench("step", obj.step, 1.0)
    print(f"  {'step':<22} {dt * 1e9:9.1f} ns/call")

    x1k = np.ones(BLOCK_1K, dtype=np.float32)
    dt = _bench("steps 1k", obj.steps, x1k, reps=max(1, REPS // 10))
    print(f"  {'steps 1k':<22} {dt * 1e6:9.3f} µs  ({BLOCK_1K / dt / 1e6:.1f} MSa/s)")
    x64k = np.ones(BLOCK_64K, dtype=np.float32)
    dt = _bench("steps 64k", obj.steps, x64k, reps=max(1, REPS // 100))
    print(f"  {'steps 64k':<22} {dt * 1e3:9.3f} ms  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)")


if __name__ == "__main__":
    main()
```

These files are the starting point — add domain-specific assertions for your
algorithm's actual behaviour. The scaffold tests verify the API contract
(construction, type safety, getter/setter round-trips, reset, lifecycle);
correctness tests are yours to write.

Run them with:

```sh
make test        # CTest + pytest (all tests)
make bench       # C timing loop + Python perf_counter suite
```
