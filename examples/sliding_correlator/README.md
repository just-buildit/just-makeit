# sliding_correlator example

A sliding correlator computes the cross-correlation between a running input
window and a fixed reference sequence:

```
y[n] = Σ  conj(ref[k]) · x[n−k]
       k=0..N-1
```

When `ref` equals the complex conjugate of a known waveform, the output peaks
whenever that waveform appears in the input.  Practical uses include preamble
detection, CDMA despreading, and radar/sonar matched filtering.

The goal of this example is to show that `JM_DEFINE_STEPS` is algorithm-agnostic:
the correlator uses complex multiply (not real×complex like FIR), a different
state layout, and different semantics — and the macro handles it identically.

## TL;DR — see it work first

```sh
git clone https://github.com/just-buildit/just-makeit
cd just-makeit
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
python3 examples/sliding_correlator/test.py
# sliding_correlator: PASSED
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
just-makeit new my_corr \
    --object sliding_correlator \
    --state "ref:float _Complex[16]" \
    --state "delay:float _Complex[16]"
cd my_corr
```

---

## 2. Implement `sliding_correlator_step`

Open `native/inc/sliding_correlator/sliding_correlator_core.h` and replace the
stub with the implementation below.

The stub:

```c
// before
static inline float complex sliding_correlator_step(const sliding_correlator_state_t *state, float complex x) {
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}
```

The implementation:

```c
// after
static inline float complex sliding_correlator_step(sliding_correlator_state_t *state, float complex x) {
    memmove(&state->delay[1], &state->delay[0], 15 * sizeof(float complex));
    state->delay[0] = x;

    float complex acc = 0.0f + 0.0f * I;
    for (int k = 0; k < 16; k++)
        acc += conjf(state->ref[k]) * state->delay[k];
    return acc;
}
```

Or apply the patch:

```python
"""Patch sliding_correlator_step stub with the implementation.

Run from the project root: python3 .steps/02_patch.py
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/sliding_correlator/sliding_correlator_core.h")
impl = pathlib.Path(__file__).with_name("02_step_after.c")

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float complex\s*\n"
    r"sliding_correlator_step\((?:const )?sliding_correlator_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
m = stub_re.search(text)
if not m:
    print("ERROR: stub not found — already patched or file changed", file=sys.stderr)
    sys.exit(1)

qualifier = m.group(1)
replacement = impl.read_text().strip().replace("static inline", qualifier, 1)
patched = stub_re.sub(replacement, text)
header.write_text(patched)
print(f"patched {header}")
```

---

## 3. Build and verify

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Debug \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
cmake --build build --target test ARGS="--output-on-failure"
pip install -e .
```

Quick sanity check — identity correlator (ref = [1, 0, …, 0]) passes the
input through unchanged:

```python
import numpy as np
from my_corr import SlidingCorrelator

c = SlidingCorrelator()
ref = np.zeros(16, dtype=np.complex64)
ref[0] = 1.0
c.set_ref(ref)

impulse = np.zeros(16, dtype=np.complex64)
impulse[0] = 1.0
print(c.steps(impulse)[:4].tolist())
# [(1+0j), 0j, 0j, 0j]
```

---

## 4. `JM_DEFINE_STEPS`

From inside `my_corr`:

```sh
# run from inside my_corr
just-makeit perf
```

Add `CORR_TAPS`, `CORR_LENGTH`, `CORR_BATCH`, and `sliding_correlator_step_batch()`
to `native/inc/sliding_correlator/sliding_correlator_core.h` just after
`sliding_correlator_step()`:

```c
#define CORR_TAPS   16
#define CORR_LENGTH (CORR_TAPS - 1)
/* Complex samples per step_batch() call.  Same derivation as FIR_BATCH:
 * JM_SIMD_WIDTH_F32/2 ≥ 1 on AVX2/AVX-512; _JM_STEPS_SIMD_ is a no-op
 * on scalar targets so the value there doesn't matter. */
#define CORR_BATCH  (JM_SIMD_WIDTH_F32 / 2)

/* No ISA guard needed — _JM_STEPS_SIMD_ only calls this when width > 1.
 * The inner loop is auto-vectorisable; the compiler picks the best ISA. */
JM_FORCEINLINE JM_HOT void
sliding_correlator_step_batch(
    sliding_correlator_state_t *state,
    const float complex        *window,
    float complex              *out)
{
    for (int b = 0; b < CORR_BATCH; b++) {
        float complex acc = 0.0f + 0.0f * I;
        for (int k = 0; k < CORR_TAPS; k++)
            acc += conjf(state->ref[k]) * window[b + CORR_LENGTH - k];
        out[b] = acc;
    }
}
```

`CORR_LENGTH = CORR_TAPS - 1` is the history depth — the quantity `JM_DEFINE_STEPS`
needs.  `CORR_TAPS` and `CORR_BATCH` are algorithm-specific; the macro never
sees them directly.

`step_batch()` computes the same dot product as `step()` but reads from a
pre-built contiguous window instead of the delay line, so the inner loop has
no scatter-gather and the compiler can auto-vectorise it freely.  No `#ifdef`
guard is needed: `_JM_STEPS_SIMD_` only calls `step_batch()` when
`JM_SIMD_WIDTH_F32 > 1`, so scalar builds simply use the `step()` path.

Replace `sliding_correlator_steps` in `native/src/sliding_correlator/sliding_correlator_core.c`:

```c
#define CORR_CHUNK 256

JM_DEFINE_STEPS(sliding_correlator, sliding_correlator_state_t, float complex,
                CORR_LENGTH, CORR_BATCH, CORR_CHUNK)
```

`JM_DEFINE_STEPS` generates `sliding_correlator_steps()` — scratch buffer,
chunked fill, SIMD dispatch (AVX-512 or AVX2), scalar tail.  The three
constants keep each concern separate:

| constant      | concern      | meaning                                             |
|---------------|--------------|-----------------------------------------------------|
| `CORR_LENGTH` | algorithm    | history depth (`delay[]` entries)                   |
| `CORR_BATCH`  | parallelism  | complex samples per call (`JM_SIMD_WIDTH_F32 / 2`)  |
| `CORR_CHUNK`  | tuning       | samples per scratch-buffer fill                     |
