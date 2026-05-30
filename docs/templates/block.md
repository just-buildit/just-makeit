# `jm object NAME --block` — block transform (array → array)

**Status: proposed.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
`--block` flag would bundle `--arg-type "T[]"` + `--return-type "T[]"`
with a `_core.c` skeleton that contains the loop pre-written.

## Command

```sh
jm object NAME --block \
    --elem-type "float _Complex" \
    --state gain:float:1.0f
```

`--elem-type` is the per-sample type; the array form (`T[]`) is implied
by `--block`.

## What you get

### `native/inc/NAME/NAME_core.h` (proposed)

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

### `native/src/NAME/NAME_core.c` (proposed)

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
opaque pointer (declared with `opaque = true` in TOML, or with the
proposed `--opaque` flag in Phase 2).

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

## Concrete types

| Slot                       | Accepts                                                                         | Rejects                                                                                                                           | Default           |
| -------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `--elem-type` (per-sample) | Any element type in the [array element table](../types.md#array-element-types). | `bool`, `int` (use `int32_t`), `const char *`, `long double _Complex` — no canonical numpy dtype.                                 | `float _Complex`  |
| `--state field:T:D`        | Any [scalar](../types.md#state-variable-types).                                 | `const char *`.                                                                                                                   | `gain:float:1.0f` |
| `--return-type`            | Not accepted — block always emits the same element type as `--elem-type`.       | All values. For width-changing transforms (e.g. `float[]` → `int16_t[]`) use a [library](library.md) function with `--out-param`. | —                 |
