# `jm object NAME --blockwise` — blockwise processor (array → array)

A **blockwise processor** is a processor whose unit of work is a block
of samples rather than one sample at a time: array in, array out, each
output element typically computed from one or more input elements plus
state.

Concrete examples: an FFT, an overlap-save filter, a CSV row
transformer that re-encodes a batch, an image kernel applied across a
row, or any algorithm where the per-element cost is dominated by a
shared setup that you'd rather do once per block.

**Status: not yet available.** A blockwise scaffold needs an array
*return* type (`--return-type "T[]"`), which just-makeit does not
support yet. There is no `--preset blockwise`, and passing
`--return-type "T[]"` errors cleanly at parse time rather than
generating a broken project. Array *input* (`--arg-type "T[]"`) works
today — only the array-out half is missing.

This page documents the intended shape and shows the workaround that
already covers the most common case (plan-once / execute-many).

## The workaround that works today

The block pattern that matters most — heavy setup once, fast `steps()`
per block — needs no array *return* type. Declare the algorithm with a
sized output param and a scalar `steps()` over an input array; the
plan-once-execute-many recipe below is the production shape and builds
today.

## What a blockwise preset would generate (proposed)

### `native/inc/NAME/NAME_core.h`

```c
typedef struct {
    float gain;
} NAME_state_t;

NAME_state_t *NAME_create(float gain);
void          NAME_destroy(NAME_state_t *state);
void          NAME_reset(NAME_state_t *state);

void NAME_steps(NAME_state_t       *state,
                const float complex *in, size_t n,
                float complex      *out);
```

There is no inline `step()` — block transforms operate on whole
buffers, so the public surface is `steps()` alone.

### `native/src/NAME/NAME_core.c`

```c
void
NAME_steps(NAME_state_t       *state,
           const float complex *in, size_t n,
           float complex      *out)
{
    /* TODO: process n samples from in[] into out[].
       The default body below is a unity-gain pass-through with
       a state->gain multiplier. Replace with your block algorithm. */
    for (size_t i = 0; i < n; i++) {
        out[i] = state->gain * in[i];
    }
}
```

## What you fill in

The body of the `for` loop. Common shapes:

- FIR filter — accumulate into a tap-delay buffer in state, multiply by
    coefficients.
- FFT — call `fftwf_execute` on a pre-planned plan stored in state.
- Block remap — pure data shuffling (deinterleave, complex conjugate,
    swap halves).

### The plan-once-execute-many pattern (FFT, correlator)

DSP libraries with heavy construction (FFTW plans, pre-computed twiddle
tables, vendor-opaque handles like `pocketfft_plan`) fit the block
preset cleanly — the heavy work lives in `create_impl`, the hot path
stays a `steps()` call. The state struct carries the plan as an
[opaque pointer](../types.md#opaque-state-fields-pointers-handles)
(declared with `opaque = true` in TOML).

```c
typedef struct {
    fftwf_plan plan;        /* heavyweight; built once in create() */
    size_t     n;
    float _Complex *scratch;
} NAME_state_t;

NAME_state_t *
NAME_create(size_t n)
{
    NAME_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj) return NULL;
    obj->n       = n;
    obj->scratch = fftwf_alloc_complex(n);
    obj->plan    = fftwf_plan_dft_1d(n, obj->scratch, obj->scratch,
                                     FFTW_FORWARD, FFTW_MEASURE);
    return obj;
}

void
NAME_steps(NAME_state_t *state,
           const float _Complex *in, size_t n,
           float _Complex       *out)
{
    /* TODO: copy in→scratch, execute plan, copy scratch→out.
       Hot path — no allocations, no plan rebuilding. */
    memcpy(state->scratch, in, n * sizeof(*in));
    fftwf_execute(state->plan);
    memcpy(out, state->scratch, n * sizeof(*out));
}
```

This is the same shape doppler's `fft` and `corr` components use —
opaque vendor plans in state, block-shaped `steps()` calls in the
hot path. No new preset needed.

## Python usage

```python
import numpy as np
from <pkg> import NAME

xform = NAME(gain=1.0)
out = xform.steps(np.ones(1024, dtype=np.complex64))   # → (1024,) complex64
```

## Concrete types (proposed)

These slots describe the future `--preset blockwise` shape; `--elem-type`
is not a real flag yet.

| Slot                       | Accepts                                                                                                                                   | Rejects                                                                                           | Default           |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------- |
| `--elem-type` (per-sample) | Any element type in the [array element table](../types.md#array-element-types).                                                           | `bool`, `int` (use `int32_t`), `const char *`, `long double _Complex` — no canonical numpy dtype. | `float _Complex`  |
| `--state field:T:D`        | Any [scalar](../types.md#state-variable-types).                                                                                           | `const char *`.                                                                                   | `gain:float:1.0f` |
| `--return-type`            | Array return unsupported. For width-changing transforms (`float[]` → `int16_t[]`) use a [function](function.md) with `--out-param` today. | All values.                                                                                       | —                 |
