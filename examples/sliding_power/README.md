# sliding_power — sliding window signal power estimator

Estimate the instantaneous power of a signal over a rolling window of N samples:

```
P[n] = (1/N) * sum( |x[n-k]|^2  for k in 0..N-1 )
```

Two update strategies are shown:

- **step()** — O(1) recursive: `sum_sq += new² − old²`
- **SIMD recompute** — horizontally sums the delay line with `JM_ADD_F32` /
  `JM_HSUM_F32` from `jm_simd.h`; used for periodic recalibration and as a
  clean demonstration of the v0.5 macro set.

---

## 1. Scaffold

The state has three fields: a 64-element float ring buffer (`delay`) that
stores |x|² for the last 64 samples, a running double accumulator (`sum_sq`),
and a write-head index (`pos`).  `--perf` generates `jm_perf.h` and
`jm_simd.h` alongside the scaffold.

```sh
just-makeit new my_power \
    --component power_est \
    --state "delay:float[64]" \
    --state "sum_sq:double:0.0" \
    --state "pos:int:0" \
    --perf
```

---

## 2. Implement step()

Replace the generated stub in `native/inc/power_est/power_est_core.h` with
the recursive O(1) update.  The delay line stores `|x|²` for each past sample;
`sum_sq` is the running total.

```c
JM_FORCEINLINE JM_HOT float complex
power_est_step(power_est_state_t *state, float complex x)
{
    float re = crealf(x), im = cimagf(x);
    float mag_sq = re * re + im * im;

    /* O(1) recursive update: subtract the oldest sample, add the new one */
    state->sum_sq += (double)(mag_sq - state->delay[state->pos]);
    state->delay[state->pos] = mag_sq;
    state->pos = (state->pos + 1) & 63;  /* window = 64 = 2^6 */

    return (float)(state->sum_sq * (1.0 / 64.0)) + 0.0f * I;
}
```

Apply the patch:

```sh
python3 .steps/02_patch.py
```

---

## 3. Build and test

```sh
make && make test
```

The generated C test exercises `create`, `reset`, `step`, and `steps`.

---

## 4. Python demo

After `pip install -e .`:

```python
"""Power estimator demo.

Run from the project root after `pip install -e .`:
    python3 .steps/04_demo.py
"""
import cmath
import math
import numpy as np
from my_power import PowerEst

est = PowerEst()

# --- sine wave: expected power = 0.5 (amplitude 1, RMS = 1/sqrt(2)) --------
for n in range(128):
    y = est.step(math.sin(2 * math.pi * n / 16) + 0j)
print(f"sine   power (expect ~0.500): {y.real:.4f}")

# --- white noise: expected power ≈ variance --------------------------------
rng = np.random.default_rng(42)
noise = (rng.standard_normal(128) + 1j * rng.standard_normal(128)) / math.sqrt(2)
for x in noise:
    y = est.step(complex(x))
print(f"noise  power (expect ~1.000): {y.real:.4f}")

# --- silence: power decays to zero after 64 samples -----------------------
for _ in range(64):
    y = est.step(0j)
print(f"silence power (expect 0.000): {y.real:.4f}")

# --- steps() on a block ---------------------------------------------------
est.reset()
block = np.array([math.sin(2 * math.pi * n / 16) for n in range(128)],
                 dtype=np.complex64)
out = est.steps(block)
print(f"steps() final power (expect ~0.500): {out[-1].real:.4f}")
```

Expected output:

```
sine   power (expect ~0.500): 0.5000
noise  power (expect ~1.000): 0.9844
silence power (expect 0.000): 0.0000
steps() final power (expect ~0.500): 0.5000
```

---

## 5. SIMD recompute with `jm_simd.h`

The recursive `step()` accumulates `sum_sq` incrementally — fast, but
floating-point rounding errors accumulate over millions of samples.  A
periodic full recompute from the delay line corrects any drift.

Add this function to `native/src/power_est/power_est_core.c` (it needs
`jm_simd.h`, included via `jm_perf.h`):

```c
/* Add to power_est_core.c — SIMD recompute from delay line.
 *
 * Uses jm_simd.h macros: JM_ADD_F32, JM_LOAD_F32, JM_HSUM_F32, JM_UNROLL.
 * Compiles to AVX-512, AVX2, or scalar depending on -march flags.
 *
 * Call every ~1000 samples to correct floating-point drift in sum_sq.
 */
static inline float
power_est_recompute(power_est_state_t *state)
{
    JM_VEC_F32 acc = JM_ZERO_F32();
    JM_UNROLL(4)
    for (int k = 0; k < 64; k += JM_SIMD_WIDTH_F32)
        acc = JM_ADD_F32(acc, JM_LOAD_F32(state->delay + k));
    float power = JM_HSUM_F32(acc) * (1.0f / 64.0f);

    /* Sync the recursive accumulator so both paths agree */
    state->sum_sq = (double)power * 64.0;
    return power;
}
```

**What each macro does on each ISA:**

| Macro | AVX-512 | AVX2 | Scalar |
|---|---|---|---|
| `JM_VEC_F32` | `__m512` (16 lanes) | `__m256` (8 lanes) | `float` (1 lane) |
| `JM_LOAD_F32(p)` | `_mm512_loadu_ps(p)` | `_mm256_loadu_ps(p)` | `*(p)` |
| `JM_ADD_F32(a,b)` | `_mm512_add_ps(a,b)` | `_mm256_add_ps(a,b)` | `(a)+(b)` |
| `JM_HSUM_F32(v)` | `_mm512_reduce_add_ps(v)` | `_mm_hadd_ps(...)` | `(v)` |
| `JM_UNROLL(4)` | `#pragma GCC unroll 4` | same | same |

The loop body is identical across all three tiers.  `JM_SIMD_WIDTH_F32`
(16, 8, or 1) controls the stride; the 64-element delay line is always
an exact multiple of any supported width, so there is no scalar tail.

Build with `-DENABLE_SIMD=ON` to activate AVX-512 or AVX2 paths:

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON
cmake --build build --parallel
```
