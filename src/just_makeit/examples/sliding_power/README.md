# sliding_power — sliding window signal power estimator

Estimate the instantaneous power of a signal over a rolling window of N samples:

```
P[n] = (1/N) * sum( |x[n-k]|^2  for k in 0..N-1 )
```

Two update strategies are shown:

- **step()** — O(1) recursive: `sum_sq += new² − old²`
- **SIMD recompute** — horizontally sums the delay line with `JM_ADD_F32` /
  `JM_HSUM_F32` from `jm_simd.h`; used for periodic recalibration and as a
  clean demonstration of the `jm_simd.h` macro set.

The generated `.pyi` class docstring is lifted from the `@brief` on
`my_power_power_est_create()` in the sacred header; section 2 replaces the
scaffold's generic one with a real sentence.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example sliding_power
# sliding_power: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which installs just-makeit and then builds the toolchain
venv at `/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold

The state has three fields: a 64-element float ring buffer (`delay`) that
stores |x|² for the last 64 samples, a running double accumulator (`sum_sq`),
and a write-head index (`pos`).  `--return-type float` makes `step()` return
the power as a real number (the input stays `float _Complex`).  `--perf`
generates `jm_perf.h` and `jm_simd.h` alongside the scaffold.

```sh
just-makeit new my_power \
    --object power_est \
    --return-type float \
    --state "delay:float[64]" \
    --state "sum_sq:double:0.0" \
    --state "pos:int:0" \
    --perf
```

---

## 2. Implement step()

Replace the generated stub in `native/inc/my_power/power_est/power_est_core.h` with
the recursive O(1) update.  The delay line stores `|x|²` for each past sample;
`sum_sq` is the running total.

```c
JM_FORCEINLINE JM_HOT float
my_power_power_est_step (my_power_power_est_state_t *state, float _Complex x)
{
  float re = crealf (x), im = cimagf (x);
  float mag_sq = re * re + im * im;

  /* O(1) recursive update: subtract the oldest sample, add the new one */
  state->sum_sq += (double)(mag_sq - state->delay[state->pos]);
  state->delay[state->pos] = mag_sq;
  state->pos               = (state->pos + 1) & 63; /* window = 64 = 2^6 */

  return (float)(state->sum_sq * (1.0 / 64.0));
}
```

Apply it with the patch script that ships with just-makeit, in this
example's `.steps/` directory. Run it from the project root by that path
(the later sections reuse `STEPS`):

```sh
STEPS="$(python3 -c 'import just_makeit, pathlib; print(pathlib.Path(just_makeit.__file__).parent / "examples/sliding_power/.steps")')"
python3 "$STEPS/02_patch.py"
```

While the header is open, give the Python class a real docstring: replace
the scaffold's `@brief Create a power_est instance.` above
`my_power_power_est_create()` with your own sentence (for example
`@brief Create a sliding-window signal-power estimator over a 64-sample
window, zeroed.`), then re-derive the `.pyi` from it. `apply` leaves your
`step()` body alone:

```sh
just-makeit apply
```

---

## 3. Build and test

```sh
make && make test
```

The generated C test exercises `create`, the state accessors, `step`,
`reset`, and `destroy`.

---

## 4. Python demo

`make` built the extension into `src/my_power/`; an editable install points
Python there (it compiles nothing). Then run the demo below:

```sh
pip install -e .
python3 "$STEPS/04_demo.py"
```

```python
"""Power estimator demo.

Run after `pip install -e .` (STEPS is this example's .steps/ directory
inside the installed just-makeit; the README shows how to set it):
    python3 "$STEPS/04_demo.py"
"""

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
noise = (rng.standard_normal(128) + 1j * rng.standard_normal(128)) / math.sqrt(
    2
)
for x in noise:
    y = est.step(complex(x))
print(f"noise  power (expect ~1.000): {y.real:.4f}")

# --- silence: power decays to zero after 64 samples -----------------------
for _ in range(64):
    y = est.step(0j)
print(f"silence power (expect 0.000): {y.real:.4f}")

# --- steps() on a block ---------------------------------------------------
est.reset()
block = np.array(
    [math.sin(2 * math.pi * n / 16) for n in range(128)], dtype=np.complex64
)
out = est.steps(block)
print(f"steps() final power (expect ~0.500): {out[-1].real:.4f}")
```

Expected output:

```
sine   power (expect ~0.500): 0.5000
noise  power (expect ~1.000): 0.9135
silence power (expect 0.000): -0.0000
steps() final power (expect ~0.500): 0.5000
```

The silence line reads `-0.0000`: subtracting the samples back out of the
running sum leaves a rounding residue just below zero. Section 5 and the
numerical notes cover that drift.

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
 * Compiles to AVX-512, AVX2, NEON (AArch64) or scalar, depending on the
 * target and -march flags.
 *
 * Call every ~1000 samples to correct floating-point drift in sum_sq.
 */
static inline float
power_est_recompute (my_power_power_est_state_t *state)
{
  JM_VEC_F32 acc = JM_ZERO_F32 ();
  JM_UNROLL (4)
  for (int k = 0; k < 64; k += JM_SIMD_WIDTH_F32)
    acc = JM_ADD_F32 (acc, JM_LOAD_F32 (state->delay + k));
  float power = JM_HSUM_F32 (acc) * (1.0f / 64.0f);

  /* Sync the double accumulator so both paths agree */
  state->sum_sq = (double)power * 64.0;
  return power;
}
```

**What each macro does on each ISA:**

| Macro             | AVX-512                   | AVX2                 | AArch64 NEON             | Scalar           |
| ----------------- | ------------------------- | -------------------- | ------------------------ | ---------------- |
| `JM_VEC_F32`      | `__m512` (16 lanes)       | `__m256` (8 lanes)   | `float32x4_t` (4 lanes)  | `float` (1 lane) |
| `JM_LOAD_F32(p)`  | `_mm512_loadu_ps(p)`      | `_mm256_loadu_ps(p)` | `vld1q_f32(p)`           | `*(p)`           |
| `JM_ADD_F32(a,b)` | `_mm512_add_ps(a,b)`      | `_mm256_add_ps(a,b)` | `vaddq_f32(a,b)`         | `(a)+(b)`        |
| `JM_HSUM_F32(v)`  | `_mm512_reduce_add_ps(v)` | `_mm_hadd_ps(...)`   | `vaddvq_f32(v)`          | `(v)`            |
| `JM_UNROLL(4)`    | `#pragma GCC unroll 4`    | same                 | same                     | same             |

The loop body is identical across all four tiers.  `JM_SIMD_WIDTH_F32`
(16, 8, 4, or 1) controls the stride; the 64-element delay line is always
an exact multiple of any supported width, so there is no scalar tail.

On x86-64, build with `-DENABLE_SIMD=ON` to activate the AVX-512 or AVX2
path (AArch64 needs no flag: NEON is always on there):

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON
cmake --build build --parallel
```

---

## Numerical notes

**The three approaches compute the same quantity** — they differ only in
how they maintain the running sum.

| Approach                       | Cost per sample | Drift               |
| ------------------------------ | --------------- | ------------------- |
| Standard MA (full recompute)   | O(N) adds       | none — always exact |
| Recursive O(1), float32 acc    | O(1)            | grows as √n         |
| Recursive O(1), double acc     | O(1)            | negligible          |
| Recursive + periodic recompute | O(1) amortised  | bounded             |

**Why drift happens in the recursive form:**
Each `step()` does `sum_sq += new² − old²`.  On paper this is exact, but in
floating-point the subtraction can cancel significant bits, and the residual
error accumulates.  Over N samples the error grows roughly as
`sqrt(N) * eps * sum_sq`.

**Why it barely matters with a double accumulator:**
The delay line holds `float` values (4-byte, ~7 decimal digits).  The
accumulator is `double` (8-byte, ~15 digits).  The inputs only carry 7
digits of information, so the accumulator's extra precision absorbs all
rounding residuals.  Over 5 million noise samples the error stays at
~10⁻¹⁵ — below the float32 quantisation noise floor of ~10⁻⁷.

**When the SIMD recompute earns its keep:**
If the accumulator were `float` (e.g., for an embedded target with no FPU
double path), drift reaches ~7×10⁻⁵ by 5M samples and keeps climbing.  A
full recompute every 1000 samples (4 AVX-512 vectors over the 64-sample
line) resets the error to zero.

You can reproduce these numbers yourself (pure Python, no build needed;
`STEPS` as set in section 2):

```sh
python3 "$STEPS/06_compare.py"
```

Output (excerpt; 5M noise samples):

```
Demo 2: float32 accumulator
    sample      no-cal  calibrated      std MA   err(no-cal)    err(cal)
   500,000      0.9821      0.9821      0.9821      1.66e-06    0.00e+00
 5,000,000      1.2276      1.2277      1.2277      6.89e-05    0.00e+00

Demo 3: double accumulator (what the C code uses)
    sample   recursive      std MA           err
   500,000      0.9821      0.9821      8.88e-16
 5,000,000      1.2277      1.2277      8.88e-16
```
