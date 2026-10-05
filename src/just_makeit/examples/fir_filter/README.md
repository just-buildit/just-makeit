# fir_filter example

A 16-tap, real-coefficient FIR filter that processes complex (I/Q) signals.
Follow along to scaffold, implement, build, and use it yourself.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example fir_filter
# fir_filter: PASSED
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

Open `native/inc/my_fir/fir_filter/fir_filter_core.h` and replace the `my_fir_fir_filter_step` stub.
The filter must update the delay line, so the signature changes from `const` to mutable:

```c
// before
static inline float _Complex my_fir_fir_filter_step (
    const my_fir_fir_filter_state_t *state, float _Complex x)
{
  (void)state; /* TODO: implement using state variables */
  return (float _Complex)x;
}
```

```c
// after
static inline float _Complex my_fir_fir_filter_step (
    my_fir_fir_filter_state_t *state, float _Complex x)
{
  /* Shift delay line — oldest sample falls off the end */
  memmove (&state->delay[1], &state->delay[0],
           (16 - 1) * sizeof (float _Complex));
  state->delay[0] = x;

  /* Convolve: y = sum_k( coeffs[k] * delay[k] ) */
  float _Complex y = 0.0f + 0.0f * I;
  for (int k = 0; k < 16; k++)
    y += state->coeffs[k] * state->delay[k];

  return (float _Complex)state->gain * y;
}
```

`my_fir_fir_filter_steps()` in `fir_filter_core.c` loops over this automatically —
no changes needed there.

---

## 3. Build and test

```sh
make
make test
```

The generated tests cover getter/setter round-trips, reset behaviour, the
context manager, and destroy. After implementing the filter you can add
signal-level tests (the impulse responses in sections 4 and 5 are ready-made
assertions).

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
#include "my_fir/fir_filter/fir_filter_core.h"
#include <complex.h>
#include <stdio.h>

int
main (void)
{
  my_fir_fir_filter_state_t *f = my_fir_fir_filter_create (1.0f);

  float h[16] = { 0 };
  h[0]        = 0.25f;
  h[1]        = 0.5f;
  h[2]        = 0.25f;
  my_fir_fir_filter_set_coeffs (f, h);

  /* Read taps without copying — pointer valid until
   * my_fir_fir_filter_destroy(f) */
  const float *view = my_fir_fir_filter_get_coeffs_view (f);
  printf ("h[1] = %.2f\n", view[1]); /* 0.50 */

  /* Feed a unit impulse */
  float _Complex in[16]  = { 0 };
  float _Complex out[16] = { 0 };
  in[0]                  = 1.0f + 0.0f * I;
  my_fir_fir_filter_steps (f, in, out, 16);

  printf ("out[0]=%.2f  out[1]=%.2f  out[2]=%.2f\n", crealf (out[0]),
          crealf (out[1]), crealf (out[2])); /* 0.25  0.50  0.25 */

  /* Snapshot the delay line — independent copy */
  float _Complex dl[16];
  my_fir_fir_filter_get_delay (f, dl);
  printf ("delay[0] = %.3f + %.3fj\n", crealf (dl[0]), cimagf (dl[0]));

  my_fir_fir_filter_reset (
      f); /* clears delay and coeffs, restores gain = 1.0f */
  my_fir_fir_filter_destroy (f);
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
just-makeit add --force --state n_taps:int32_t:16
make test
```

State is structural, so `add` rebuilds the object from the manifest: the
`my_fir_fir_filter_state_t` struct and lifecycle are regenerated and your
`my_fir_fir_filter_step()` body is reset to a fresh stub. `add` asks before
it deletes and regenerates the object's files; `--force` skips the prompt.
Re-run the implement step (section 2) to restore the kernel on top of the
new state. The same applies when you swap in a longer delay line:

```sh
just-makeit add --force --state "coeffs64:double _Complex[64]"
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
RUNS = 500

f = FirFilter(gain=1.0)
h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
f.set_coeffs(h)
signal = (np.random.randn(BLOCK) + 1j * np.random.randn(BLOCK)).astype(
    np.complex64
)

elapsed = min(timeit.repeat(lambda: f.steps(signal), number=RUNS, repeat=5))
print(f"{RUNS * BLOCK / elapsed / 1e6:.1f} M complex samples/sec")
```

Build baseline, measure, rebuild with SIMD, measure again:

```sh
# Baseline build (no SIMD). ENABLE_SIMD is a cached option, so say OFF
# explicitly: a configure that omits it keeps the last run's ON.
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=OFF \
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

The numbers below were measured on one AVX-512 machine (`bench.py` reports
the best of five timed repeats). Yours will differ; the ratios are what
carries over.

### Round 1 — flags alone

The section 2 kernel shifts the delay line with `memmove`.
Adding `-march=native -ffast-math` via `ENABLE_SIMD=ON` gives a modest gain:

```
baseline:  65.1 M complex samples/sec
with SIMD: 90.6 M complex samples/sec   (1.4×)
```

The ceiling is the `memmove` of 120 bytes (15 `float _Complex`) that runs every
sample.  The vectoriser can auto-vectorise the 16-tap MAC, but it can't overlap
that store with the accumulate.  Flags alone don't get you there.

### Round 2 — algorithm matters

Three concerns, three places.  `jm_perf.h` ships a `JM_DEFINE_STEPS` macro
that stamps out the outer dispatch loop so you never write it by hand.

**1.** Add the constants and `my_fir_fir_filter_step_batch()` to
`native/inc/my_fir/fir_filter/fir_filter_core.h` just after `my_fir_fir_filter_step()`:

```c
#define FIR_TAPS 16 /* algorithm:   number of coefficients       */
#define FIR_LENGTH                                                            \
  (FIR_TAPS - 1) /* history:     samples held in delay[]      */
/* JM_SIMD_WIDTH_F32 floats = JM_SIMD_WIDTH_F32/2 complex samples per batch.
 * On scalar targets (width=1) this is 0, and JM_STEPS_SIMD_IMPL is a no-op
 * there. */
#define FIR_BATCH (JM_SIMD_WIDTH_F32 / 2)

#if JM_SIMD_WIDTH_F32 > 1
JM_FORCEINLINE JM_HOT void
my_fir_fir_filter_step_batch (my_fir_fir_filter_state_t *state,
                              const float _Complex      *window,
                              float _Complex            *out)
{
  JM_VEC_F32 acc = JM_ZERO_F32 ();
  for (int k = 0; k < FIR_TAPS; k++)
    JM_MAC_F32 (acc, (const float *)(window + FIR_LENGTH - k),
                state->coeffs[k]);
  JM_STORE_F32 ((float *)out, JM_MUL_F32 (acc, JM_SPLAT_F32 (state->gain)));
}
#endif
```

Three named constants make each concern explicit:

| constant    | concern     | meaning                                            |
| ----------- | ----------- | -------------------------------------------------- |
| `FIR_TAPS`  | algorithm   | filter length (a compile-time constant you define) |
| `FIR_BATCH` | parallelism | complex samples per call (`JM_SIMD_WIDTH_F32 / 2`) |
| `FIR_CHUNK` | tuning      | samples per scratch-buffer fill                    |

`FIR_BATCH` is derived from `JM_SIMD_WIDTH_F32` (16 on AVX-512, 8 on AVX2,
4 on AArch64 NEON), so the same source compiles to 8, 4 or 2 complex samples
per batch without any `#ifdef`.  On scalar targets `JM_SIMD_WIDTH_F32 = 1`,
`JM_STEPS_SIMD_IMPL` is a no-op, and `step_batch()` is never called.

`step_batch()` uses `FIR_TAPS` and `FIR_BATCH`.  `steps()` uses all three —
but you never write `steps()`.

**2.** Replace `my_fir_fir_filter_steps` in `native/src/fir_filter/fir_filter_core.c`:

```c
#define FIR_CHUNK 256 /* tuning: samples per scratch-buffer fill */

JM_DEFINE_STEPS (my_fir_fir_filter, my_fir_fir_filter_state_t, float _Complex,
                 FIR_LENGTH, FIR_BATCH, FIR_CHUNK)
```

`JM_DEFINE_STEPS` generates `my_fir_fir_filter_steps()` from the macro in `jm_perf.h`:
it owns the scratch buffer, the chunked fill, and the scalar tail.  You write
`step()`.  You write `step_batch()`.  The rest is infrastructure.

```
baseline:    64.7 M complex samples/sec   (unchanged: the batch path is compiled out)
with SIMD: 1315.1 M complex samples/sec   (20× the baseline)
```

The baseline does not move.  Without `ENABLE_SIMD=ON` an x86-64 build has no
AVX tier, so `JM_SIMD_WIDTH_F32` is 1, `JM_STEPS_SIMD_IMPL` expands to
nothing, and `steps()` is the same scalar loop over the `memmove` `step()`
as round 1.  With `ENABLE_SIMD=ON` the scratch-buffer path runs:
`step_batch()` handles `FIR_BATCH` complex samples per call (8 on AVX-512,
4 on AVX2) over an L1-resident chunk, with no per-sample `memmove`.
`jm_simd.h` selects the tier at compile time, no source changes needed.  On
AArch64, NEON is always available, so even the baseline build takes the
batch path there (2 complex samples per call).

---

## 8. Document once, in the header

The `@brief` on `my_fir_fir_filter_create()` in the sacred header is the single source
of truth for the class docstring — edit it and `jm apply` re-derives the `.pyi`
summary from it, so the stub reads like real documentation instead of the
generic "FirFilter component." fallback:

```python
"""Enrich the sacred ``fir_filter_core.h`` header with a real class summary.

The header is the single source of truth for documentation: ``jm`` parses the
``/** ... */`` comment on ``my_fir_fir_filter_create`` and turns its ``@brief`` into
the summary line of the generated ``.pyi`` class docstring. Out of the box the
scaffold brief ("Create a fir_filter instance.") is generic, so jm falls back
to a bland "FirFilter component." summary. Replacing it with a real sentence
here makes the stub read like documentation. Run this after ``jm perf`` /
``jm add`` have settled the header; a follow-up ``jm apply`` re-derives the
``.pyi`` from the edited comment.

Usage, from the project root (STEPS is this example's .steps/ directory
inside the installed just-makeit; the README shows how to set it):
    python3 "$STEPS/08_doxygen.py"
"""

from __future__ import annotations

import pathlib
import re

from just_makeit import _csym  # gh-1591: the derived symbol stem
import sys

OBJ = "fir_filter"
CREATE_BRIEF = (
    "A 16-tap real-coefficient FIR filter for complex (I/Q) signals,"
    " with a scalar output gain."
)


def main() -> None:
    header = pathlib.Path("native/inc/my_fir") / OBJ / f"{OBJ}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real one.
    scaffold_re = re.compile(
        rf"/\*\*\n \* @brief Create a {OBJ} instance\..*?"
        rf"(?={_csym.stem(header, OBJ)}_state_t \*{_csym.stem(header, OBJ)}_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {CREATE_BRIEF}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        print(
            f"ERROR: {OBJ} create() scaffold brief not found", file=sys.stderr
        )
        sys.exit(1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


if __name__ == "__main__":
    main()
```

The script ships with just-makeit, in this example's `.steps/` directory.
Run it from the project root by that path, then `jm apply`:

```sh
STEPS="$(python3 -c 'import just_makeit, pathlib; print(pathlib.Path(just_makeit.__file__).parent / "examples/fir_filter/.steps")')"
python3 "$STEPS/08_doxygen.py"
just-makeit apply
```
