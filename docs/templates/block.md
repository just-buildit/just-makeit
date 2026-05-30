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

## Python usage

```python
import numpy as np
from <pkg> import NAME

xform = NAME(gain=1.0)
out = xform.steps(np.ones(1024, dtype=np.complex64))   # → (1024,) complex64
```

## Concrete types

| Slot                       | Accepts                                                                                                                                   | Default in this template |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| `--elem-type` (per-sample) | Any element type from the [array element table](../types.md#array-element-types). `bool` and `const char *` are not legal array elements. | `float _Complex`         |
| `--state field:T:D`        | Any [scalar](../types.md#state-variable-types).                                                                                           | `gain:float:1.0f`        |

The block preset doesn't accept a separate `--return-type` — the
output element type is always the same as `--elem-type`. If you need a
different output element width (e.g. quantising `float[]` → `int16_t[]`),
use a [library](library.md) function with an `--out-param` instead.
