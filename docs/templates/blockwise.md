# `jm object NAME --preset blockwise` — blockwise processor (array → array)

A **blockwise processor** is a processor whose unit of work is a block of
samples rather than one sample at a time: array in, array out, each output
element typically computed from one or more input elements plus state.

Concrete examples: an FFT, an overlap-save filter, a CSV row transformer
that re-encodes a batch, an image kernel applied across a row, or any
algorithm where the per-element cost is dominated by a shared setup that
you'd rather do once per block.

The blockwise preset is distinct from the [processor](processor.md) preset in
one important way: **there is no inline `step()` function**. Block transforms
operate on whole buffers; the public C and Python surface is `steps()` alone.
You implement one function and get Python, type stubs, tests, and benchmarks
for free.

## Command

```sh
jm new my_dsp
cd my_dsp
jm object my_xform --preset blockwise --state gain:float:1.0f
```

`--preset` is a `jm object` flag, not a `jm new` one, so scaffold the project
first, then add the object. The default element type is `float _Complex`
(complex64). Override with explicit `--arg-type` and `--return-type` flags:

```sh
# real-valued blockwise transform: float[] → float[]
jm object my_filter --preset blockwise \
    --arg-type "float[]" --return-type "float[]"

# heterogeneous: raw int16 in, normalised float out
jm object my_conv --preset blockwise \
    --arg-type "int16_t[]" --return-type "float[]"
```

## What you get

### `native/inc/my_xform/my_xform_core.h`

```c
typedef struct {
    float gain;
} my_xform_state_t;

my_xform_state_t *my_xform_create(float gain);
void              my_xform_destroy(my_xform_state_t *state);
void              my_xform_reset(my_xform_state_t *state);

void
my_xform_steps(
    my_xform_state_t *state,
    const float _Complex     *in, size_t n,
    float _Complex          *out);

float my_xform_get_gain(const my_xform_state_t *state);
void  my_xform_set_gain(my_xform_state_t *state, float val);
```

### `native/src/my_xform/my_xform_core.c`

```c
void
my_xform_steps(
    my_xform_state_t *state,
    const float _Complex     *in, size_t n,
    float _Complex          *out)
{
    /* <<IMPLEMENT: blockwise transform — replace this pass-through>> */
    (void)state;
    for (size_t i = 0; i < n; i++)
        out[i] = (float _Complex)in[i];
}
```

Replace the `for` loop body with your algorithm. The stub compiles and passes
all generated CTest and pytest checks immediately — fill it in at your pace.

### `src/my_dsp/my_xform.pyi`

```python
class MyXform:
    def __init__(self, gain: np.float32 = 1.0) -> None: ...
    def reset(self) -> None: ...
    def steps(
        self,
        x: NDArray[np.complex64],
        out: NDArray[np.complex64] | None = None,
    ) -> NDArray[np.complex64]: ...
    def get_gain(self) -> np.float32: ...
    def set_gain(self, value: np.float32) -> None: ...
    def destroy(self) -> None: ...
```

## Python usage

```python
import numpy as np
from my_dsp import MyXform

xform = MyXform(gain=1.0)

# Allocates and returns a fresh output array
out = xform.steps(np.ones(1024, dtype=np.complex64))
# out.shape == (1024,), out.dtype == complex64

# Caller-supplied buffer (zero-copy on the Python side)
buf = np.empty(1024, dtype=np.complex64)
ret = xform.steps(np.ones(1024, dtype=np.complex64), buf)
assert ret is buf
```

## Concrete types

| Slot              | Accepts                                                                                                                                                         | Rejects                                                                                                  | Default            |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------ |
| `--arg-type`      | Any `T[]` where `T` is in the [array element table](../types.md#array-element-types): `float`, `double`, `int16_t`, `float _Complex`, etc.                      | Scalar types without `[]`; `void`; unsupported element types.                                            | `float _Complex[]` |
| `--return-type`   | Any `U[]` where `U` is in the [array element table](../types.md#array-element-types). Input and output element types may differ (e.g. `int16_t[]` → `float[]`). | Scalar types without `[]`; `void`; unsupported element types; `T[]` without a matching `--arg-type T[]`. | `float _Complex[]` |
| `--state field:T` | Any [scalar state type](../types.md#state-variable-types). For opaque heap buffers (plans, scratch arrays) use `opaque = true` in TOML.                         | `const char *`.                                                                                          | (no state vars)    |

## What you fill in

The body of the `for` loop in `steps()`. Common shapes:

- **FIR filter** — accumulate into a tap-delay buffer held in state, dot-product with coefficients.
- **Gain / scale** — multiply each input sample by `state->gain`.
- **FFT** — call `fftwf_execute` on a pre-computed plan stored in state.
- **Block remap** — deinterleave, complex-conjugate, swap halves — pure data shuffling.

### Plan-once / execute-many pattern (FFT, correlator)

DSP libraries with heavy construction (FFTW plans, pocketfft plans, vendor
opaque handles) fit blockwise cleanly — heavy work in `create_impl`, hot path
stays a `steps()` call. The plan is an
[opaque pointer](../declarative-scaffolding.md#opaque-state-fields-pointers-and-handles)
in the state struct.

```toml
# objects/fft.toml
[fft]
arg_type     = "float _Complex[]"
return_type  = "float _Complex[]"
create_impl  = """
obj->n       = n;
obj->scratch = fftwf_alloc_complex(n);
obj->plan    = fftwf_plan_dft_1d((int)n, obj->scratch, obj->scratch,
                                 FFTW_FORWARD, FFTW_MEASURE);
if (!obj->scratch || !obj->plan) { free(obj); return NULL; }
"""
destroy_impl = """
if (state->plan)    fftwf_destroy_plan(state->plan);
if (state->scratch) fftwf_free(state->scratch);
"""

[[fft.init_params]]
name = "n"
type = "size_t"

[[fft.state]]
name   = "plan"
type   = "fftwf_plan"
opaque = true

[[fft.state]]
name   = "scratch"
type   = "float _Complex *"
opaque = true

[[fft.state]]
name    = "n"
type    = "size_t"
default = "0"
```

Then implement `steps()` in `fft_core.c`:

```c
void
fft_steps(fft_state_t        *state,
          const float _Complex *in, size_t n,
          float _Complex       *out)
{
    memcpy(state->scratch, in, n * sizeof(*in));
    fftwf_execute(state->plan);
    memcpy(out, state->scratch, n * sizeof(*out));
}
```

## See also

- [Types reference — array element types](../types.md#array-element-types)
- [Declarative scaffolding — opaque state](../declarative-scaffolding.md#opaque-state-fields-pointers-and-handles)
- [array_processing example](../examples/array_processing.md) — array input with scalar output (reduction shape)
- [Template gallery index](index.md)
