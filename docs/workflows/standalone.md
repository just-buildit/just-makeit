# Scenario 1 — Simple standalone extension

*You're here if:* you have one algorithm to wrap — a filter, a running
statistic, an oscillator — and you want it available as `from my_dsp import Engine` with full C and Python tests.

A single C object exposed as a Python extension. Good starting point for
wrapping an algorithm, DSP primitive, or performance-critical inner loop.

## 1. Scaffold

```sh
just-makeit new my_dsp \
    --object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0
cd my_dsp
```

`--arg-type` and `--return-type` set the C types for `step()`'s input and
output. Omit both and they default to `float _Complex`.

## 2. Implement

Open `native/inc/gain/gain_core.h` and fill in the `gain_step` stub:

```c
static inline float
gain_step(const gain_state_t *state, float x)
{
    return state->gain * x;
}
```

`gain_steps()` — the block processor — is already in `gain_core.c` and loops
over this automatically. You do not edit the Python binding (`gain_ext.c`).

## 3. Build and test

```sh
make        # cmake configure + build (Release)
make test   # CTest (C lifecycle) + unittest (Python API)
```

## 4. Install

> **Note:** Run inside an activated virtual environment. Use
> `jm-install-deps path/to/venv && source path/to/venv/bin/activate`
> to create one with all build deps included.

```sh
python3 -m pip install .          # build wheel + install
python3 -m pip install -e .       # editable install (Python-only edits take effect immediately)
```

## 5. Use from Python

```python
import numpy as np
from my_dsp import Gain

g = Gain(gain=2.0)

# single sample
y = g.step(1.0)              # → 2.0

# block
x = np.ones(1024, dtype=np.float32)
y = g.steps(x)               # → float32 ndarray, all 2.0

# getters / setters
g.set_gain(0.5)
g.get_gain()                 # → 0.5

# reset to declared defaults
g.reset()

# context manager
with Gain(gain=2.0) as g:
    y = g.steps(x)
```

## 6. Optional: performance annotations

Once the algorithm is working and tested:

```sh
just-makeit perf
make
```

Patches `step()` with `JM_FORCEINLINE JM_HOT`, writes `jm_perf.h` and
`jm_simd.h`, and records the setting so future `object` and `add` calls
inherit it. See [Performance annotations](../perf.md) for the full reference.
