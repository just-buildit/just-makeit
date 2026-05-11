# filter_module example

A two-type filter library where `Fir` (FIR filter) and `Biquad` (biquad IIR)
live together in a single `filter` Python extension module.

Before this workflow, every component produced its own `.so`:
```
my_filters/fir.cpython-312-x86_64-linux-gnu.so
my_filters/biquad.cpython-312-x86_64-linux-gnu.so
```

With `module` + `object`, related types share one `.so` as a proper subpackage:
```
my_filters/filter/filter.cpython-312-x86_64-linux-gnu.so
my_filters/filter/__init__.py   ← re-exports Fir, Biquad
```

Users import cleanly:
```python
from my_filters.filter import Fir, Biquad
```

## TL;DR — see it work first

```sh
curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh
source /tmp/jm-venv/bin/activate
just-makeit example filter_module
# filter_module: PASSED
```

## Prerequisites

```sh
curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh | sh -s -- ~/my-venv
source ~/my-venv/bin/activate
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit
just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold the project

```sh
just-makeit new my_filters
cd my_filters
```

`just-makeit new` with no `--object` creates the project scaffold only:
`CMakeLists.txt`, `pyproject.toml`, `just-makeit.toml`, and the `native/`
directory tree — but no component yet.  Types come next.

---

## 2. Create the module

```sh
just-makeit module filter
```

`just-makeit module filter` scaffolds the grouping unit:

| Created | Purpose |
|---------|---------|
| `native/src/filter/filter_ext.c` | C extension — empty, no types yet |
| `native/src/filter/CMakeLists.txt` | Python module target (no object libs yet) |
| `src/my_filters/filter/__init__.py` | Subpackage init — empty exports |

`just-makeit.toml` gains:

```toml
[module.filter]
objects = []
```

The module is a named slot.  Types are added with `just-makeit object`.

---

## 3. Add the types

```sh
just-makeit object fir \
    --module filter \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]" \
    --state "gain:float:1.0"

just-makeit object biquad \
    --module filter \
    --arg-type float \
    --return-type float \
    --state "b0:double:1.0" \
    --state "b1:double:0.0" \
    --state "b2:double:0.0" \
    --state "a1:double:0.0" \
    --state "a2:double:0.0" \
    --state "w1:double:0.0" \
    --state "w2:double:0.0"
```

`just-makeit object` does two things for each type:

**Per-object C library** (same as `just-makeit object`, no Python module target):

| File | Purpose |
|------|---------|
| `native/inc/fir/fir_core.h` | Header: struct, inline `fir_step`, getters/setters |
| `native/src/fir/fir_core.c` | Source: create/destroy/reset/steps |
| `native/src/fir/CMakeLists.txt` | OBJECT library + C test + bench (no `.so`) |
| `native/tests/test_fir_core.c` | C test with `CHECK` macro counter |
| `native/benchmarks/bench_fir_core.c` | C benchmark |

**Module regeneration** — after each `just-makeit object`, these are fully rewritten:

| File | What changes |
|------|-------------|
| `native/src/filter/filter_ext.c` | `FirObject` type added; `PyMODINIT_FUNC` registers it |
| `native/src/filter/CMakeLists.txt` | `fir_core` added to link list |
| `src/my_filters/filter/__init__.py` | `from .filter import Fir` added |

After both objects:

```toml
[module.filter]
objects = ["fir", "biquad"]
```

```python
# src/my_filters/filter/__init__.py — generated
from .filter import Fir, Biquad

__all__ = ["Fir", "Biquad"]
```

`filter_ext.c` contains both `FirObject` and `BiquadObject` type definitions
followed by a single `PyInit_filter` that registers both.

### Fir state

| Name     | Type                 | Default | Role |
|----------|----------------------|---------|------|
| `coeffs` | `float[16]`          | zeros   | Tap weights |
| `delay`  | `float _Complex[16]` | zeros   | Input history |
| `gain`   | `float`              | `1.0`   | Output scalar |

### Biquad state (Direct Form II transposed, real-valued)

`Biquad` uses `--arg-type float --return-type float` — real signals, double-precision
arithmetic.  A module can host types with different I/O types; `Fir` is complex,
`Biquad` is real.

| Name | Type     | Default | Role |
|------|----------|---------|------|
| `b0` | `double` | `1.0`   | Feed-forward coefficient |
| `b1` | `double` | `0.0`   | Feed-forward coefficient |
| `b2` | `double` | `0.0`   | Feed-forward coefficient |
| `a1` | `double` | `0.0`   | Feed-back coefficient |
| `a2` | `double` | `0.0`   | Feed-back coefficient |
| `w1` | `double` | `0.0`   | Delay state (double for numerical headroom) |
| `w2` | `double` | `0.0`   | Delay state |

---

## 4. Implement

### FIR filter

Open `native/inc/fir/fir_core.h` and replace `fir_step`.  The delay line
is mutated, so the signature drops `const`:

```c
static inline float complex
fir_step(fir_state_t *state, float complex x)
{
    memmove(&state->delay[1], &state->delay[0],
            (16 - 1) * sizeof(float complex));
    state->delay[0] = x;

    float complex y = 0.0f;
    for (int k = 0; k < 16; k++)
        y += state->coeffs[k] * state->delay[k];
    return (float complex)state->gain * y;
}
```

### Biquad filter (Direct Form II transposed, real)

Open `native/inc/biquad/biquad_core.h` and replace `biquad_step`.
Delay states `w1`/`w2` are written each call, so `const` drops here too:

```c
static inline float
biquad_step(biquad_state_t *state, float x)
{
    double y   = state->b0 * (double)x + state->w1;
    state->w1  = state->b1 * (double)x - state->a1 * y + state->w2;
    state->w2  = state->b2 * (double)x - state->a2 * y;
    return (float)y;
}
```

`double` arithmetic avoids coefficient-quantisation noise accumulation in the
delay states; the output is narrowed back to `float` on return.

> **Note:** both `fir_steps()` and `biquad_steps()` in their respective
> `_core.c` files loop over `_step()` automatically — no changes needed there.

---

## 5. Build and test

```sh
cmake -B build -S . \
    -DCMAKE_BUILD_TYPE=Release \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
ctest --test-dir build --output-on-failure
pip install -e .
```

CMake builds one Python extension module (`filter.cpython-*.so`) inside the
`src/my_filters/filter/` subpackage directory.  It links `fir_core` and
`biquad_core` OBJECT libraries — no separate `fir.so` or `biquad.so` anywhere.

CTest runs the two C tests:

```
test_fir_core    PASSED
test_biquad_core PASSED
```

Both use the `CHECK` macro counter — failures print file/line and exit nonzero
regardless of `-DNDEBUG`.

The installed package layout:

```
src/my_filters/
  __init__.py
  filter/
    __init__.py                              ← from .filter import Fir, Biquad
    filter.cpython-312-x86_64-linux-gnu.so  ← both types in one .so
```

---

## 6. Use from Python

```python
"""Demo: Fir (complex) and Biquad (real) from the filter module."""

import math
import sys

sys.path.insert(0, "src")

import numpy as np
from my_filters.filter import Biquad, Fir

# ── FIR: 16-tap complex low-pass (windowed sinc, cutoff = 0.1 * fs) ─────────
N = 16
h = np.array(
    [
        math.sin(math.pi * 0.1 * (k - N // 2)) / (math.pi * (k - N // 2))
        if k != N // 2
        else 0.1
        for k in range(N)
    ],
    dtype=np.float32,
)
h /= h.sum()

fir = Fir(gain=1.0)
fir.set_coeffs(h)

impulse = np.zeros(N, dtype=np.complex64)
impulse[0] = 1.0
ir = fir.steps(impulse)
print("FIR impulse response (first 4):", ir[:4].real.round(4))

# ── Biquad: real low-pass at cutoff = 0.1 * fs, Q = 0.707 ───────────────────
fc, Q = 0.1, 0.707
w0    = 2 * math.pi * fc
alpha = math.sin(w0) / (2 * Q)
c     = math.cos(w0)
a0    = 1 + alpha

bq = Biquad(
    b0=(1 - c) / 2 / a0,
    b1=(1 - c)     / a0,
    b2=(1 - c) / 2 / a0,
    a1=-2 * c      / a0,
    a2=(1 - alpha) / a0,
)

t   = np.arange(512, dtype=np.float32) / 512
lo  = np.cos(2 * math.pi * 0.05 * t)   # 0.05*fs — passband
hi  = np.cos(2 * math.pi * 0.40 * t)   # 0.40*fs — stopband

out_lo = bq.steps(lo)
bq.reset()
out_hi = bq.steps(hi)

print(f"Biquad passband power:  {np.mean(out_lo**2):.3f}  (expect ≈ 0.5)")
print(f"Biquad stopband power:  {np.mean(out_hi**2):.5f} (expect << 0.5)")

# ── Both types from one import ───────────────────────────────────────────────
print("\nBoth types live in the same module:")
print(f"  {Fir}    — complex I/Q")
print(f"  {Biquad} — real float")
```

Both types come from the same import:

```python
from my_filters.filter import Fir, Biquad
```

`Fir` and `Biquad` are fully independent — no shared state, separate
`create`/`destroy` lifecycles, each with its own `step`, `steps`, `reset`,
and context manager support.

### Adding a third type later

```sh
just-makeit object iir --module filter \
    --state "sos:double[20]" \
    --state "zi:double[10]"
```

`filter_ext.c`, `filter/CMakeLists.txt`, and `filter/__init__.py` are all
regenerated automatically.  `Fir` and `Biquad` are unaffected — the module
`_ext.c` is always rebuilt from the full object list, not patched.
