# `jm function FN --module MOD` — function

## Generalist 2

`jm function` materializes **pure C + Python module-level functions**
— one of just-makeit's two generalist templates (see
[gallery overview](index.md)). No class, no state, no lifecycle —
distinct from `jm object`'s stateful-class generalist.

A function takes inputs and (optionally) writes to output buffers.
Customizations are flags that shape its signature — `--out-param`
for writable output buffers, `--out-type` for allocate-and-return
ndarrays, `--result-field` for record-returning shapes, `--inline`
for header-only emission. Multiple `jm function` calls into the same
module compose into a library of utilities — that's the "library"
pattern, achieved by repetition rather than by a third generalist.

Concrete examples: a pure unit conversion (Q15→float, Celsius→Kelvin,
bytes→hex), a lookup-table query, a one-shot format detector, a CRC,
a string normaliser, or any pure computation where a per-call object
would be overkill.

`jm function` and its `--out-param` flag shipped in **0.13.22**;
`--out-type` and `--result-field` ship in **0.13.23**. The example
below uses real generated output.

## Command

```sh
jm new my_dsp
cd my_dsp
jm module io
jm function q15_to_float --module io \
    --param input:int16_t[] \
    --out-param output:float[] \
    --param n:size_t
```

`--param` declares input arrays (auto `const`-qualified) and scalar
params; `--out-param` declares writable output arrays (`const`
dropped). After scaffolding, fill in the `/* TODO */` body in
`_core.c`.

## TOML written

Functions live inside a module fragment. The command above writes
the module fragment to `modules/io.toml`. Hand-author the fragment
and `jm apply` — both paths produce identical files.

```toml
# modules/io.toml
objects = []

[[functions]]
name = "q15_to_float"

[[functions.params]]
name = "input"
type = "int16_t[]"

[[functions.params]]
name = "output"
type = "float[]"
out = true

[[functions.params]]
name = "n"
type = "size_t"
```

Adding more functions = appending more `[[functions]]` entries to
the same fragment. Copying a whole module to another project = copying
`modules/io.toml` and the `native/inc/io/`, `native/src/io/`,
`src/<pkg>/io/` paths together.

## What you get

### `native/inc/io/io_core.h` (declaration — glue, regenerated)

```c
void q15_to_float(const int16_t *input,  size_t input_len,
                  float         *output, size_t output_len,
                  size_t n);
```

`input` is `const`; `output` is not. Pure glue — jm regenerates the
header from TOML whenever the function signature changes.

### `native/src/io/q15_to_float.c` (sacred — your body lives here)

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

## Extending the initial command

`jm function` is single-shot: every customization is a flag on the
initial command. Base = the Command above; below shows the same
base in both shell and TOML form (no NEW lines here — the base is
already fully composed):

=== "Shell"

    ```sh
    jm function q15_to_float --module io \
        --param input:int16_t[] \
        --out-param output:float[] \
        --param n:size_t
    ```

=== "TOML"

    ```toml
    # modules/io.toml
    objects = []

    [[functions]]
    name = "q15_to_float"

    [[functions.params]]
    name = "input"
    type = "int16_t[]"

    [[functions.params]]
    name = "output"
    type = "float[]"
    out = true

    [[functions.params]]
    name = "n"
    type = "size_t"
    ```

What each flag contributes:

- `--out-param output:float[]` — writable output array; `const` is
    dropped on the generated declaration so you can write into the
    buffer.

The function body lives in `q15_to_float.c`. Open the file, replace
the `/* TODO */` marker with your math. There is no flag for lifting
bodies from elsewhere.

### Growing the module — adding more functions

Each function added grows the module by one sacred `.c` file.
Existing files (sacred or glue) are never touched by the addition.
CLI for one-offs; TOML for batches.

=== "One-off via CLI"

    ```sh
    # Binding allocates and returns the output ndarray (no caller output buffer)
    jm function envelope --module io \
        --param input:float[] \
        --out-type float
    # → creates native/src/io/envelope.c (sacred, with /* TODO */)
    # → regenerates io_core.h, io_ext.c, io.pyi (glue)
    # → q15_to_float.c untouched

    # Return a list of records (event emitter)
    jm function find_peaks --module io \
        --param input:float[] \
        --variable-output --max-out 64 \
        --result-field idx:size_t \
        --result-field magnitude:float
    # → creates native/src/io/find_peaks.c (sacred)
    # → regenerates io_core.h, io_ext.c, io.pyi

    # Header-only inline (no .c entry; inlines at -O2 in callers)
    jm function lerp --module io \
        --param a:float --param b:float --param t:float \
        --return-type float \
        --inline
    # → declaration goes into io_core.h directly; no .c file
    ```

=== "Many at once via TOML"

    ```toml
    # modules/io.toml — append the blocks below
    [[functions]]
    name = "envelope"
    out_type = "float"

    [[functions.params]]
    name = "input"
    type = "float[]"

    [[functions]]
    name = "find_peaks"
    return_type = "size_t"
    variable_output = true
    max_out = 64

    [[functions.params]]
    name = "input"
    type = "float[]"

    [[functions.result_fields]]
    name = "idx"
    type = "size_t"

    [[functions.result_fields]]
    name = "magnitude"
    type = "float"

    [[functions]]
    name = "lerp"
    return_type = "float"
    inline = true

    [[functions.params]]
    name = "a"
    type = "float"

    [[functions.params]]
    name = "b"
    type = "float"

    [[functions.params]]
    name = "t"
    type = "float"
    ```

A library of utilities is built by running `jm function` multiple
times. Each call adds a new sacred `.c` file; existing function
bodies stay untouched. This is the structural reason modules grow
cleanly under sacred-files: each function is its own file.

### Cross-cutting: perf

`jm perf` retrofits `JM_HOT` / `JM_FORCEINLINE` onto hot-path
functions across the whole project (objects + module-level
functions). Project-wide, reversible (`--off`), idempotent.

### The resulting module

After the calls above, the `io` module exposes:

```python
from my_dsp.io import q15_to_float, envelope, find_peaks, lerp

q15_to_float(int16_arr, float_arr, n)      # caller-allocated output
env = envelope(input_arr)                  # binding-allocated output
peaks = find_peaks(input_arr)              # list[(idx, magnitude)]
y = lerp(a, b, t)                          # inline pure function
```

The C surface, the binding, the tests, and the `.pyi` stub all stay
in sync.

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
