# fir_filter example

A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.
Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
just-makeit example fir_filter
# fir_filter: PASSED
```

## Prerequisites

```sh
pip install just-makeit
just-makeit install-deps --check   # report what is installed vs. what will be installed
just-makeit install-deps           # install cmake, C compiler, numpy, and create a venv
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
just-makeit install-deps ~/my-venv && source ~/my-venv/bin/activate
```

---

## 1. Scaffold

```sh
just-makeit new my_fir \
    --object fir_filter \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]" \
    --state "gain:float:1.0"
```

Three state variables:

| Name     | Type                 | Role                         | Constructor param?           |
| -------- | -------------------- | ---------------------------- | ---------------------------- |
| `coeffs` | `float[16]`          | Real tap weights             | No — load via `set_coeffs()` |
| `delay`  | `float _Complex[16]` | Complex delay line (history) | No — zero on create/reset    |
| `gain`   | `float`              | Output scalar gain           | Yes — default `1.0`          |

`coeffs` and `delay` are inline in the C struct — no heap allocation per field.

---

## 2. Implement

Open `native/inc/fir_filter/fir_filter_core.h` and replace the `fir_filter_step` stub.
The filter must update the delay line, so the signature changes from `const` to mutable:

```c
// before
static inline float complex fir_filter_step(const fir_filter_state_t *state, float complex x) {
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}
```

```c
// after
static inline float complex fir_filter_step(fir_filter_state_t *state, float complex x) {
    /* Shift delay line — oldest sample falls off the end */
    memmove(&state->delay[1], &state->delay[0], (16 - 1) * sizeof(float complex));
    state->delay[0] = x;

    /* Convolve: y = sum_k( coeffs[k] * delay[k] ) */
    float complex y = 0.0f + 0.0f * I;
    for (int k = 0; k < 16; k++)
        y += state->coeffs[k] * state->delay[k];

    return (float complex)state->gain * y;
}
```

`fir_filter_steps()` in `fir_filter_core.c` loops over this automatically —
no changes needed there.

---

## 3. Build and test

```sh
make
make test
```

The generated tests cover getter/setter round-trips, reset behaviour, the
context manager, and destroy. After implementing the filter you can add
signal-level tests (see step 5).

---

## 4. Try it from Python

```sh
pip install -e .
```

```python
import numpy as np
from my_fir import FirFilter

f = FirFilter(gain=1.0)

# Load a 3-tap averager into the first three taps
h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
f.set_coeffs(h)

# Inspect taps without copying — read-only view, zero allocation
view = f.get_coeffs_view()
print("writeable:", view.flags["WRITEABLE"])  # False
print("h[1]:", view[1])  # 0.5

# Feed a unit impulse and read back the impulse response
impulse = np.zeros(16, dtype=np.complex64)
impulse[0] = 1.0
y = f.steps(impulse)
print("impulse response:", y[:4].real)  # [0.25 0.5  0.25 0.  ]

# Snapshot the delay line — independent copy, safe to keep indefinitely
dl = f.get_delay()
print("delay[0]:", dl[0])

# Context manager ensures destroy() on exit
with FirFilter(gain=2.0) as g:
    g.set_coeffs(h)
    y2 = g.steps(impulse)
print("gain=2 response:", y2[:3].real)  # [0.5 1.  0.5]
```

---

## 5. Try it from C

After `make`, the combined shared library is at `build/libmy_fir.so`.

```c
// demo.c
#include "fir_filter/fir_filter_core.h"
#include <complex.h>
#include <stdio.h>

int main(void) {
    fir_filter_state_t *f = fir_filter_create(1.0f);

    float h[16] = {0};
    h[0]        = 0.25f;
    h[1]        = 0.5f;
    h[2]        = 0.25f;
    fir_filter_set_coeffs(f, h);

    /* Read taps without copying — pointer valid until fir_filter_destroy(f) */
    const float *view = fir_filter_get_coeffs_view(f);
    printf("h[1] = %.2f\n", view[1]); /* 0.50 */

    /* Feed a unit impulse */
    float complex in[16]  = {0};
    float complex out[16] = {0};
    in[0]                 = 1.0f + 0.0f * I;
    fir_filter_steps(f, in, out, 16);

    printf("out[0]=%.2f  out[1]=%.2f  out[2]=%.2f\n", crealf(out[0]), crealf(out[1]),
           crealf(out[2])); /* 0.25  0.50  0.25 */

    /* Snapshot the delay line — independent copy */
    float _Complex dl[16];
    fir_filter_get_delay(f, dl);
    printf("delay[0] = %.3f + %.3fj\n", crealf(dl[0]), cimagf(dl[0]));

    fir_filter_reset(f); /* clears delay and coeffs, restores gain = 1.0f */
    fir_filter_destroy(f);
    return 0;
}
```

```sh
gcc -O2 -std=c99 -Inative/inc demo.c \
    -Lbuild -lmy_fir -Wl,-rpath,build \
    -lm -o demo && ./demo
```

---

## 6. Add more state

```sh
just-makeit add --state n_taps:int32_t:16
make test
```

Or swap in a longer delay line without touching your implementation:

```sh
just-makeit add --state "coeffs64:double _Complex[64]"
```

---

## 7. Bonus: `--perf` + SIMD benchmark

From inside `my_fir`:

```sh
# run from inside my_fir
just-makeit perf
```

Save the benchmark below as `bench.py`:

```python
import timeit
import numpy as np
from my_fir import FirFilter

BLOCK = 100_000
RUNS  = 500

f = FirFilter(gain=1.0)
h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
f.set_coeffs(h)
signal = (np.random.randn(BLOCK) + 1j * np.random.randn(BLOCK)).astype(np.complex64)

elapsed = min(timeit.repeat(lambda: f.steps(signal), number=RUNS, repeat=5))
print(f"{RUNS * BLOCK / elapsed / 1e6:.1f} M complex samples/sec")
```

Build baseline, measure, rebuild with SIMD, measure again:

```sh
# Baseline build (no SIMD)
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
pip install -e . --force-reinstall
python3 bench.py

# Rebuild with SIMD
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
pip install -e . --force-reinstall
python3 bench.py
```

### Round 1 — flags alone

The scaffold's default implementation shifts the delay line with `memmove`.
Adding `-march=native -ffast-math` via `ENABLE_SIMD=ON` gives a modest gain:

```
baseline:  106.8 M complex samples/sec
with SIMD: 154.1 M complex samples/sec   (1.4×)
```

The ceiling is the `memmove` of 120 bytes (15 `float complex`) that runs every
sample.  The vectoriser can auto-vectorise the 16-tap MAC, but it can't overlap
that store with the accumulate.  Flags alone don't get you there.

### Round 2 — algorithm matters

Three concerns, three places.  `jm_perf.h` ships a `JM_DEFINE_STEPS` macro
that stamps out the outer dispatch loop so you never write it by hand.

**1.** Add the constants and `fir_filter_step_batch()` to
`native/inc/fir_filter/fir_filter_core.h` just after `fir_filter_step()`:

```c
#define FIR_TAPS   16              /* algorithm:   number of coefficients       */
#define FIR_LENGTH (FIR_TAPS - 1)  /* history:     samples held in delay[]      */
/* JM_SIMD_WIDTH_F32 floats = JM_SIMD_WIDTH_F32/2 complex samples per batch.
 * On scalar targets (width=1) this is 0; _JM_STEPS_SIMD_ is a no-op there. */
#define FIR_BATCH  (JM_SIMD_WIDTH_F32 / 2)

#if JM_SIMD_WIDTH_F32 > 1
JM_FORCEINLINE JM_HOT void
fir_filter_step_batch(
    fir_filter_state_t     *state,
    const float complex    *window,
    float complex          *out)
{
    JM_VEC_F32 acc = JM_ZERO_F32();
    for (int k = 0; k < FIR_TAPS; k++)
        JM_MAC_F32(acc, (const float *)(window + FIR_LENGTH - k), state->coeffs[k]);
    JM_STORE_F32((float *)out, JM_MUL_F32(acc, JM_SPLAT_F32(state->gain)));
}
#endif
```

Three named constants make each concern explicit:

| constant    | concern      | meaning                                           |
|-------------|--------------|---------------------------------------------------|
| `FIR_TAPS`  | algorithm    | filter length (set at codegen time)               |
| `FIR_BATCH` | parallelism  | complex samples per call (`JM_SIMD_WIDTH_F32 / 2`) |
| `FIR_CHUNK` | tuning       | samples per scratch-buffer fill                   |

`FIR_BATCH` is derived from `JM_SIMD_WIDTH_F32` (16 on AVX-512, 8 on AVX2),
so the same source compiles to 8 or 4 complex samples per batch without any
`#ifdef`.  On scalar targets `JM_SIMD_WIDTH_F32 = 1`, `_JM_STEPS_SIMD_` is a
no-op, and `step_batch()` is never called.

`step_batch()` uses `FIR_TAPS` and `FIR_BATCH`.  `steps()` uses all three —
but you never write `steps()`.

**2.** Replace `fir_filter_steps` in `native/src/fir_filter/fir_filter_core.c`:

```c
#define FIR_CHUNK 256  /* tuning: samples per scratch-buffer fill */

JM_DEFINE_STEPS(fir_filter, fir_filter_state_t, float complex,
                FIR_LENGTH, FIR_BATCH, FIR_CHUNK)
```

`JM_DEFINE_STEPS` generates `fir_filter_steps()` from the macro in `jm_perf.h`:
it owns the scratch buffer, the chunked fill, and the scalar tail.  You write
`step()`.  You write `step_batch()`.  The rest is infrastructure.

```
baseline:   475 M complex samples/sec
with SIMD: 1745 M complex samples/sec   (3.7×)
```

The scalar baseline is already 4.5× faster than the `memmove` version because
sequential scratch accesses are hardware-prefetcher-friendly; the L1-resident
chunk eliminates the circular-buffer index arithmetic entirely.  Adding
`ENABLE_SIMD=ON` delivers the full speedup from AVX-512's 16-wide float FMA
(3.7×) or AVX2's 8-wide FMA — `jm_simd.h` selects the best tier at compile
time, no source changes needed.

---

## 8. Pure variant — caller-managed params

The stateful FIR (steps 1–7) uses an opaque `fir_filter_state_t *` that
just-makeit allocates and owns.  For the **pure** variant, the caller owns the
`params_t` struct directly — multiple channels can share the same algorithm
without any allocation inside the hot path.

### Scaffold

```sh
cd ..
just-makeit new my_fir_pure \
    --object fir_pure \
    --pure \
    --param "coeffs:float[16]" \
    --param "delay:float _Complex[16]"
cd my_fir_pure
```

`--pure` with array state (`float[16]`) auto-selects the **struct** style.
just-makeit generates:

| Generated | What it is |
|-----------|-----------|
| `fir_pure_params_t` | Non-opaque struct; layout is public |
| `fir_pure_params_create()` | Heap alloc + zero-init (`calloc`; comment shows `aligned_alloc` for SIMD) |
| `fir_pure_params_free()` | Free; safe to call with NULL |
| `fir_pure_params_init()` | In-place zero-init for stack / custom allocation |
| `fir_pure_fn(x, *p)` | Single-sample transform — `p` is modified in place |
| `fir_pure_steps(in, out, n, *p)` | Block transform |
| `FirPure` Python class | `obj(x)` calls `tp_call`; `obj.steps(arr)` calls batch path |

Because the struct is non-opaque, a caller can stack-allocate, pool-allocate,
or use `aligned_alloc` for SIMD — all without any support from the library.

### Implement

Replace the `fir_pure_fn` stub in `native/inc/fir_pure/fir_pure_core.h`:

```c
/* native/inc/fir_pure/fir_pure_core.h — replace fir_pure_fn stub */

static inline float complex
fir_pure_fn(float complex x, fir_pure_params_t *params)
{
    /* Shift delay line — oldest sample falls off the end */
    memmove(&params->delay[1], &params->delay[0],
            (16 - 1) * sizeof(float complex));
    params->delay[0] = x;

    /* Convolve: y = sum_k( coeffs[k] * delay[k] ) */
    float complex y = 0.0f;
    for (int k = 0; k < 16; k++)
        y += params->coeffs[k] * params->delay[k];
    return y;
}
```

The logic is identical to the stateful version; the only difference is the
parameter type: `fir_pure_params_t *` instead of `fir_filter_state_t *`.

### Python demo

```python
"""Demonstrate the pure FIR filter — caller owns the params."""
import numpy as np
from my_fir_pure import FirPure

# Create params — heap-allocated, reference-counted via Python GC
fir = FirPure()

# Load sinc coefficients (16-tap low-pass, cutoff ≈ 0.1 * fs)
import math
n = 16
coeffs = np.array(
    [math.sin(math.pi * 0.1 * (k - n // 2)) / (math.pi * (k - n // 2))
     if k != n // 2 else 0.1
     for k in range(n)],
    dtype=np.float32,
)
coeffs /= coeffs.sum()
fir.set_coeffs(coeffs)

# Process a 1 kHz tone at fs=8 kHz (should pass; above cutoff ≈ 800 Hz gets attenuated)
t = np.arange(1024) / 8000.0
signal = np.exp(1j * 2 * math.pi * 200.0 * t).astype(np.complex64)
out = fir.steps(signal)
print(f"in  power: {np.mean(np.abs(signal)**2):.3f}")
print(f"out power: {np.mean(np.abs(out)**2):.3f}")

# Two independent channels sharing the same algorithm — no cross-contamination
fir2 = FirPure()
fir2.set_coeffs(coeffs)
out2 = fir2.steps(signal * 2)
print(f"\nchannel 2 power (2× input): {np.mean(np.abs(out2)**2):.3f}")

# Context-manager usage (explicit resource release)
with FirPure() as f:
    f.set_coeffs(coeffs)
    y = f(signal[0])
    print(f"\ncontext-manager single sample: {y}")
```

Key differences from the stateful version:

| | Stateful (`FirFilter`) | Pure (`FirPure`) |
|--|--|--|
| Allocation | `fir_filter_create()` inside Python | `fir_pure_params_create()` inside Python |
| Process one sample | `obj.step(x)` | `obj(x)` — instance is callable |
| Multiple channels | Separate instances, separate heap allocs | Separate `FirPure` instances; params struct may be stack-allocated in C |
| State ownership | Library | Caller |
