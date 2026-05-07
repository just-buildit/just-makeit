# Example: Complex FIR Filter with Array State

This example walks through a **16-tap, real-coefficient FIR filter** that
processes complex (I/Q) signals.  It shows every array-state concept — struct
layout, constructor, reset, copy getter, read-only view, and setter — in both
C and Python.

---

## Scaffold

```sh
just-makeit new fir_filter \
    --component fir_filter \
    --state coeffs:"float[16]" \
    --state delay:"float _Complex[16]" \
    --state gain:float:1.0
```

Three state variables:

| Name     | Type                  | Role                          |
|----------|-----------------------|-------------------------------|
| `coeffs` | `float[16]`           | Real-valued tap weights       |
| `delay`  | `float _Complex[16]`  | Complex delay line (history)  |
| `gain`   | `float`               | Scalar output gain            |

`coeffs` and `delay` are always **zero-initialised** — no default may be given.
`gain` gets the declared default `1.0f` in both `create()` and `reset()`.

---

## Generated C API  (`fir_filter_core.h`)

```c
typedef struct {
    float          coeffs[16];
    float _Complex delay[16];
    float          gain;
} fir_filter_state_t;

/* lifecycle */
fir_filter_state_t *fir_filter_create(float gain);
void                fir_filter_destroy(fir_filter_state_t *state);
void                fir_filter_reset(fir_filter_state_t *state);

/* coeffs — array accessors */
void          fir_filter_get_coeffs(const fir_filter_state_t *, float *dest);
const float  *fir_filter_get_coeffs_view(const fir_filter_state_t *);
void          fir_filter_set_coeffs(fir_filter_state_t *, const float *src);

/* delay — array accessors */
void                      fir_filter_get_delay(const fir_filter_state_t *, float _Complex *dest);
const float _Complex     *fir_filter_get_delay_view(const fir_filter_state_t *);
void                      fir_filter_set_delay(fir_filter_state_t *, const float _Complex *src);

/* gain — scalar accessors */
float fir_filter_get_gain(const fir_filter_state_t *);
void  fir_filter_set_gain(fir_filter_state_t *, float gain);

/* processing stubs — implement these */
static inline float complex
fir_filter_step(const fir_filter_state_t *state, float complex x);

void fir_filter_steps(fir_filter_state_t *, const float complex *in,
                       float complex *out, size_t n);
```

The delay-line update lives in `step()` — it's not automatic.  The delay
array is part of the mutable state but `get_delay_view()` returns a
`const float _Complex *`, so callers can observe it without a copy.

---

## Implementing the filter  (`fir_filter_core.c`)

The scaffolded `_core.c` has stubs for `step()` and `steps()`.  Fill them in:

```c
#include "fir_filter/fir_filter_core.h"
#include <string.h>

/* Implements the dot-product convolution and shifts the delay line.
   Defined inline in the header for single-sample use; the non-inline
   version below is what steps() calls. */
static float _Complex
_fir_filter_step_impl(fir_filter_state_t *state, float _Complex x)
{
    /* Shift delay line: delay[1..N-1] = delay[0..N-2] */
    memmove(&state->delay[1], &state->delay[0],
            (16 - 1) * sizeof(float _Complex));
    state->delay[0] = x;

    /* Convolve: y = sum(coeffs[k] * delay[k]) */
    float _Complex y = 0.0f + 0.0f * I;
    for (int k = 0; k < 16; k++)
        y += state->coeffs[k] * state->delay[k];

    return (float _Complex)(state->gain) * y;
}

void
fir_filter_steps(fir_filter_state_t *state,
                 const float _Complex *input,
                 float _Complex       *output,
                 size_t                n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = _fir_filter_step_impl(state, input[i]);
}
```

`reset()` is already generated — it writes `memset(state->delay, 0, ...)`,
`memset(state->coeffs, 0, ...)`, and `state->gain = 1.0f`.

---

## Using the filter from C

```c
#include "fir_filter/fir_filter_core.h"
#include <complex.h>
#include <math.h>
#include <stdio.h>

int main(void)
{
    /* --- Create --- */
    fir_filter_state_t *f = fir_filter_create(1.0f);

    /* --- Load a lowpass window-sinc kernel --- */
    float h[16];
    float fc = 0.25f;  /* normalised cutoff: 0.25 × Fs */
    int   N  = 16;
    for (int k = 0; k < N; k++) {
        int n = k - N / 2;
        h[k] = (n == 0) ? 2.0f * fc
                        : sinf(2.0f * (float)M_PI * fc * n) / ((float)M_PI * n);
        h[k] *= 0.54f - 0.46f * cosf(2.0f * (float)M_PI * k / (N - 1)); /* Hamming */
    }
    fir_filter_set_coeffs(f, h);

    /* --- Inspect the taps without copying --- */
    const float *view = fir_filter_get_coeffs_view(f);
    printf("h[0] = %.6f\n", view[0]);
    /* view is valid as long as f is alive — do not free f before using view */

    /* --- Process a block of complex samples --- */
    float _Complex in[64], out[64];
    for (int i = 0; i < 64; i++)
        in[i] = cexpf(2.0f * (float)M_PI * 0.1f * i * I);  /* complex tone */

    fir_filter_steps(f, in, out, 64);
    printf("out[32] = %.3f + %.3fj\n", crealf(out[32]), cimagf(out[32]));

    /* --- Copy the delay line for inspection --- */
    float _Complex dl[16];
    fir_filter_get_delay(f, dl);
    printf("delay[0] = %.3f + %.3fj\n", crealf(dl[0]), cimagf(dl[0]));

    /* --- Reset and verify the delay is cleared --- */
    fir_filter_reset(f);
    fir_filter_get_delay(f, dl);
    printf("after reset: delay[0] = %.3f + %.3fj\n", crealf(dl[0]), cimagf(dl[0]));

    /* --- Clean up --- */
    fir_filter_destroy(f);
    return 0;
}
```

---

## Using the filter from Python

```python
import numpy as np
from scipy.signal import firwin
from fir_filter import FirFilter

# --- Create ---
f = FirFilter(gain=1.0)

# --- Design and load a lowpass kernel ---
h = firwin(16, cutoff=0.25, window="hamming").astype(np.float32)
f.set_coeffs(h)

# --- Inspect the taps without copying (read-only view) ---
view = f.get_coeffs_view()
print(f"h[0] = {view[0]:.6f}")
# view.flags['WRITEABLE'] is False
# IMPORTANT: view becomes invalid after f.destroy() — never hold it past that point

# --- Process a block of complex samples ---
t = np.arange(64, dtype=np.float32)
x = np.exp(2j * np.pi * 0.1 * t).astype(np.complex64)

y = f.steps(x)                  # block processing via the generated binding
print(f"y[32] = {y[32]:.3f}")

# --- Copy the delay line for inspection / serialisation ---
dl = f.get_delay()              # np.complex64 ndarray, length 16 — independent copy
print(f"delay[0] = {dl[0]:.3f}")

# --- Context manager ensures destroy() is called ---
with FirFilter(gain=1.0) as g:
    g.set_coeffs(h)
    y2 = g.steps(x)
# g.handle is now NULL; g.get_coeffs_view() would raise RuntimeError("destroyed")

# --- Reset and verify ---
f.reset()
assert f.get_delay()[0] == 0j
assert f.get_coeffs()[0] == 0.0   # coeffs are also zeroed on reset
```

---

## Concepts illustrated

### Array struct layout

Arrays are stored **inline** in the state struct — no heap allocation per array.
One `malloc` covers all state; one `free` releases it all.

```c
typedef struct {
    float          coeffs[16];      /* 64 bytes */
    float _Complex delay[16];       /* 128 bytes */
    float          gain;            /* 4 bytes   */
} fir_filter_state_t;               /* ≈ 196 bytes total */
```

### Copy getter vs. view getter

| Method               | Returns                      | Allocation | Lifetime        |
|----------------------|------------------------------|-----------|-----------------|
| `get_coeffs()`       | Independent `np.float32[16]` | One malloc | Independent     |
| `get_coeffs_view()`  | Read-only view, zero-alloc   | None       | Until `destroy()` |

Use `get_coeffs_view()` in hot paths where you want to read the taps without
a memory allocation.  **Never pass the view to code that may outlive the
component** — it points directly into the C struct.

```python
# Safe — view discarded before any destroy
taps = f.get_coeffs_view()
print(taps.sum())

# Dangerous — do not do this
class State:
    def __init__(self, fir):
        self.taps = fir.get_coeffs_view()   # view stored past potential destroy!
```

### Constructor: scalars only

Array fields are zero-initialised in `fir_filter_create()` — they cannot be
set from the constructor.  Load them with `set_coeffs()` / `set_delay()` after
creating the object.

```python
# This is all __init__ can do — gain is the only constructor parameter
f = FirFilter(gain=1.0)
f.set_coeffs(h)   # load taps separately
```

### Reset semantics

`reset()` clears all array fields to zero via `memset` and restores scalar
fields to their declared defaults.  After `reset()`, the filter is in the same
state as immediately after `create()` — with an all-zero delay line and the
declared `gain` default.  Coefficients are also zeroed; reload them after reset
if you want to keep filtering.

### Limitations

- **No complex defaults via CLI.** `float _Complex` and `double _Complex`
  scalars always initialise to zero; set them programmatically after creation.
  See [State Variable Types](../types.md).
- **No partial array updates via the Python API.** `set_coeffs()` accepts an
  ndarray of exactly 16 elements — the whole array is replaced.  To update a
  single tap, copy, edit, then write back:
  ```python
  h = f.get_coeffs()
  h[3] = 0.0
  f.set_coeffs(h)
  ```
- **View lifetime is the component lifetime.** A view obtained from
  `get_coeffs_view()` becomes a dangling pointer after `destroy()`.  This is
  documented in the stub but the interpreter will not catch it.
- **Block processing is single-threaded.** `steps()` is not re-entrant — do not
  call it from multiple threads on the same instance without external locking.
