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

---

## 1. Scaffold

```sh
just-makeit new my_corr \
    --component sliding_correlator \
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
    r"sliding_correlator_step\(const sliding_correlator_state_t \*state.*?\n\}",
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
#define CORR_BATCH  8

#ifdef __AVX512F__
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
#endif
```

`CORR_LENGTH = CORR_TAPS - 1` is the history depth — the quantity `JM_DEFINE_STEPS`
needs.  `CORR_TAPS` and `CORR_BATCH` are FIR-specific; the macro never sees them.

`step_batch()` computes the same dot product as `step()` but reads from a
pre-built contiguous window instead of the delay line, so the inner loop has
no scatter-gather and the compiler can vectorise it freely.

Replace `sliding_correlator_steps` in `native/src/sliding_correlator/sliding_correlator_core.c`:

```c
#define CORR_CHUNK 256

JM_DEFINE_STEPS(sliding_correlator, sliding_correlator_state_t, float complex,
                CORR_LENGTH, CORR_BATCH, CORR_CHUNK)
```

`JM_DEFINE_STEPS` generates `sliding_correlator_steps()` — scratch buffer,
chunked fill, AVX-512 dispatch, scalar tail.  The three constants keep each
concern separate:

| constant      | concern      | meaning                             |
|---------------|--------------|-------------------------------------|
| `CORR_LENGTH` | algorithm    | history depth (`delay[]` entries)   |
| `CORR_BATCH`  | parallelism  | samples per `step_batch()` call     |
| `CORR_CHUNK`  | tuning       | samples per scratch-buffer fill     |
