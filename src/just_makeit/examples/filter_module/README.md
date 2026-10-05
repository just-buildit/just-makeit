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
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example filter_module
# filter_module: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which also works on Python 3.9 and 3.10 (the installer
needs 3.11+). It installs just-makeit, then builds the toolchain venv at
`/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
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

| Created                                      | Purpose                                                                  |
| -------------------------------------------- | ------------------------------------------------------------------------ |
| `native/src/filter/filter_ext.c`             | C extension — empty, no types yet                                        |
| `native/src/filter/CMakeLists.txt`           | Python module target, plus the module's own `filter_core` OBJECT library |
| `native/inc/my_filters/filter/filter_core.h` | Module-level C API (for `just-makeit function`)                          |
| `native/src/filter/filter_core.c`            | Module-level C implementation                                            |
| `src/my_filters/filter/__init__.py`          | Subpackage init — empty exports                                          |
| `src/my_filters/filter/filter.pyi`           | Subpackage type stub — no classes yet                                    |
| `modules/filter.toml`                        | The module's manifest fragment                                           |

`modules/filter.toml` (pulled in by `just-makeit.toml`'s `include`) holds:

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

| File                                   | Purpose                                                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `native/inc/my_filters/fir/fir_core.h` | Header: struct, inline `my_filters_fir_step`, getters/setters                                                 |
| `native/src/fir/fir_core.c`            | Source: create/destroy/reset/steps                                                                            |
| `native/src/fir/CMakeLists.txt`        | OBJECT library + C test + bench (no `.so`)                                                                    |
| `native/tests/test_fir_core.c`         | C test with `CHECK` macro counter                                                                             |
| `native/benchmarks/bench_fir_core.c`   | C benchmark                                                                                                   |
| `native/tests/test_fir_symbols.c`      | Links the address of every C function the binding calls (fails at link time if one is declared but undefined) |

It also writes the object's Python test and benchmark
(`src/my_filters/filter/tests/test_fir.py`,
`src/my_filters/filter/benchmarks/bench_fir.py`) and its manifest fragment,
`objects/fir.toml`.

**Module wiring** — each `just-makeit object` then writes or updates:

| File                                                   | What changes                                                                    |
| ------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `native/src/filter/filter_ext_fir.c`                   | Created: the `FirObject` type and its methods                                   |
| `native/src/filter/filter_ext.c`                       | `#include "filter_ext_fir.c"` added, and `PyInit_filter` registers `Fir`        |
| `native/src/filter/CMakeLists.txt`                     | `fir_core` added to the link list                                               |
| `src/my_filters/filter/__init__.py`                    | `from .filter import Fir` added                                                 |
| `src/my_filters/filter/filter.pyi`                     | `class Fir` added                                                               |
| `CMakeLists.txt`, `native/inc/my_filters/my_filters.h` | `native/src/fir` added as a subdirectory, and its header to the umbrella header |

After both objects, `modules/filter.toml`:

```toml
[module.filter]
objects = ["fir", "biquad"]
```

```python
# src/my_filters/filter/__init__.py — generated (Windows DLL-directory shim
# above this line omitted)
from .filter import Fir, Biquad  # noqa: E402

__all__ = ["Fir", "Biquad"]
```

`filter_ext.c` `#include`s `filter_ext_fir.c` and `filter_ext_biquad.c` (one
`FirObject` / `BiquadObject` each), then a single `PyInit_filter` registers
both.

### Fir state

| Name     | Type                 | Default | Role          |
| -------- | -------------------- | ------- | ------------- |
| `coeffs` | `float[16]`          | zeros   | Tap weights   |
| `delay`  | `float _Complex[16]` | zeros   | Input history |
| `gain`   | `float`              | `1.0`   | Output scalar |

### Biquad state (Direct Form II transposed, real-valued)

`Biquad` uses `--arg-type float --return-type float` — real signals, double-precision
arithmetic.  A module can host types with different I/O types; `Fir` is complex,
`Biquad` is real.

| Name | Type     | Default | Role                                        |
| ---- | -------- | ------- | ------------------------------------------- |
| `b0` | `double` | `1.0`   | Feed-forward coefficient                    |
| `b1` | `double` | `0.0`   | Feed-forward coefficient                    |
| `b2` | `double` | `0.0`   | Feed-forward coefficient                    |
| `a1` | `double` | `0.0`   | Feed-back coefficient                       |
| `a2` | `double` | `0.0`   | Feed-back coefficient                       |
| `w1` | `double` | `0.0`   | Delay state (double for numerical headroom) |
| `w2` | `double` | `0.0`   | Delay state                                 |

---

## 4. Implement

### FIR filter

Open `native/inc/my_filters/fir/fir_core.h` and replace `my_filters_fir_step`.  The delay line
is mutated, so the signature drops `const`:

```c
static inline float _Complex
my_filters_fir_step(my_filters_fir_state_t *state, float _Complex x)
{
    memmove(&state->delay[1], &state->delay[0],
            (16 - 1) * sizeof(float _Complex));
    state->delay[0] = x;

    float _Complex y = 0.0f;
    for (int k = 0; k < 16; k++)
        y += state->coeffs[k] * state->delay[k];
    return (float _Complex)state->gain * y;
}
```

### Biquad filter (Direct Form II transposed, real)

Open `native/inc/my_filters/biquad/biquad_core.h` and replace `my_filters_biquad_step`.
Delay states `w1`/`w2` are written each call, so `const` drops here too:

```c
static inline float
my_filters_biquad_step(my_filters_biquad_state_t *state, float x)
{
    double y   = state->b0 * (double)x + state->w1;
    state->w1  = state->b1 * (double)x - state->a1 * y + state->w2;
    state->w2  = state->b2 * (double)x - state->a2 * y;
    return (float)y;
}
```

`double` arithmetic avoids coefficient-quantisation noise accumulation in the
delay states; the output is narrowed back to `float` on return.

> **Note:** both `my_filters_fir_steps()` and `my_filters_biquad_steps()` in their respective
> `_core.c` files loop over `_step()` automatically — no changes needed there.

While the headers are open, the `@brief` on each object's `create()` is the
single source of truth for that class's docstring: replace the scaffold
`@brief Create a fir instance.` with a real one-line summary and `jm apply`
regenerates the module `.pyi` with it (instead of the generic `Fir component.`
fallback).

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
`src/my_filters/filter/` subpackage directory.  It links the `filter_core`,
`fir_core` and `biquad_core` OBJECT libraries — no separate `fir.so` or
`biquad.so` anywhere.

CTest runs the two C tests:

```
1/2 Test #1: test_biquad_core .................   Passed    0.00 sec
2/2 Test #2: test_fir_core ....................   Passed    0.00 sec

100% tests passed, 0 tests failed out of 2
```

Both use the `CHECK` macro counter — failures print file/line and exit nonzero
regardless of `-DNDEBUG`.

The installed package layout:

```
src/my_filters/
  __init__.py
  filter/
    __init__.py                             ← from .filter import Fir, Biquad
    filter.cpython-312-x86_64-linux-gnu.so  ← both types in one .so
    filter.pyi                              ← stubs for both types
    tests/                                  ← test_fir.py, test_biquad.py
    benchmarks/                             ← bench_fir.py, bench_biquad.py
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
w0 = 2 * math.pi * fc
alpha = math.sin(w0) / (2 * Q)
c = math.cos(w0)
a0 = 1 + alpha

lowpass = dict(
    b0=(1 - c) / 2 / a0,
    b1=(1 - c) / a0,
    b2=(1 - c) / 2 / a0,
    a1=-2 * c / a0,
    a2=(1 - alpha) / a0,
)

n = np.arange(512, dtype=np.float32)  # sample index: tones in cycles/sample
lo = np.cos(2 * math.pi * 0.05 * n)  # 0.05*fs — passband
hi = np.cos(2 * math.pi * 0.40 * n)  # 0.40*fs — stopband

# One fresh filter per tone. reset() would not do here: it restores EVERY
# state field to its declared default, the coefficients included, which
# turns this low-pass back into the b0 = 1 passthrough.
out_lo = Biquad(**lowpass).steps(lo)
out_hi = Biquad(**lowpass).steps(hi)

print(f"Biquad passband power:  {np.mean(out_lo**2):.3f}  (expect ~0.5)")
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
