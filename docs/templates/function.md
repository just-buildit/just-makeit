# `jm function FN --module MOD` — function (free C function, no class)

A **function** is a free, module-level C function exposed to Python.
No state, no class — just a named function that takes inputs and
(optionally) writes to output buffers. Multiple `jm function` calls
into the same module compose into a library of utilities.

Concrete examples: a pure unit conversion (Q15→float, Celsius→Kelvin,
bytes→hex), a lookup-table query, a one-shot format detector, a CRC,
a string normaliser, or any pure computation where a per-call object
would be overkill.

`jm function` and its `--out-param` flag shipped in **0.13.22**;
`--out-type` and `--result-field` ship in **0.13.23**. The example
below uses real generated output.

## Command

```sh
jm new my_dsp --module io
jm function q15_to_float --module io \
    --param input:int16_t[] \
    --out-param output:float[] \
    --param n:size_t \
    --impl "/tmp/impl.c::q15_to_float"
```

`--param` declares input arrays (auto `const`-qualified) and scalar
params; `--out-param` declares writable output arrays (`const`
dropped). `--impl file::funcname` lifts an existing C body into the
generated stub if you have one.

## What you get

### `native/inc/io/io_core.h` (declaration)

```c
void q15_to_float(const int16_t *input,  size_t input_len,
                  float         *output, size_t output_len,
                  size_t n);
```

`input` is `const`; `output` is not. The header and implementation
always match.

### `native/src/io/io_core.c` (stub)

```c
/* <<IMPLEMENT: q15_to_float>> */
void
q15_to_float(const int16_t *input,  size_t input_len,
             float         *output, size_t output_len,
             size_t n)
{
    (void)input; (void)input_len;
    (void)output; (void)output_len;
    (void)n;
    /* TODO: fill in the conversion. */
}
```

The Python binding (`io_ext.c`) auto-generates: numpy-array acquisition
for `input` (read-only, C-contiguous), allocation of `output` if the
caller didn't pass one (or write-through if they did), and the scalar
parsing for `n`.

## What you fill in

The function body. For Q15 → float that's two lines:

```c
void
q15_to_float(const int16_t *input,  size_t input_len,
             float         *output, size_t output_len,
             size_t n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = (float)input[i] / 32768.0f;
}
```

## Python usage

```python
import numpy as np
from my_dsp.io import q15_to_float

inp = np.arange(-32768, 32768, dtype=np.int16)
out = np.empty(inp.size, dtype=np.float32)
q15_to_float(inp, out, inp.size)
```

## Variants

- **Multiple outputs** — repeat `--out-param`.
- **Inline (header-only)** — pass `--inline` to emit a `static inline`
    body in `_core.h` so the function inlines at every call site. Good
    for short, pure functions.
- **`out_type` for sized scalar output** — when the output size is
    derivable from a single scalar param. See
    [Quick reference](../quick-reference.md) for the TOML form
    (currently the only path; a CLI flag is on the roadmap).
- **`result_fields` for record-returning functions** — declared in
    TOML today. The wizard would expose this via repeatable
    `--result-field` flags.

## Concrete types

| Slot                               | Accepts                                                                                                                                                                                                       | Rejects                                                  | Default          |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | ---------------- |
| `--param name:T`                   | Any [scalar](../types.md#module-function-param-types) or any `T[]` [array shape](../types.md#array-element-types). Arrays get `const`.                                                                        | `const char *`, `T[][]`, `string_enum:…` (object-only).  | `n:size_t`       |
| `--out-param name:T[]`             | Array shapes only. Drops `const`.                                                                                                                                                                             | All scalars (rejected at parse time per gh-72), `T[][]`. | `output:float[]` |
| `--return-type T`                  | Any [scalar](../types.md#module-function-param-types) including `void`.                                                                                                                                       | `const char *`, any `T[]`.                               | `void`           |
| `--out-type T` *(TOML only today)* | Any [array element type](../types.md#array-element-types). Sizes the returned ndarray from the first array param's length, or — when no array param is present — from the first integer scalar param (gh-65). | `bool`, `int`, `const char *`, `long double _Complex`.   | —                |

The library preset has the **narrowest** slot allowlist of any
template — no strings, no string-enums, no 2-D arrays. Need those?
Wrap the function in an object preset instead.
