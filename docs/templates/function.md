# `jm function FN --module MOD` — function (free C function, no class)

A **function** is a free, module-level C function exposed to Python.
No state, no class — just a named function that takes inputs and
(optionally) writes to output buffers. Multiple `jm function` calls
into the same module compose into a library of utilities.

Concrete examples: a pure unit conversion (Q15→float, Celsius→Kelvin,
bytes→hex), a lookup-table query, a one-shot format detector, a CRC,
a string normaliser, or any pure computation where a per-call object
would be overkill.

The example below uses real generated output.

## Command

```sh
jm new my_dsp --module io
jm function q15_to_float --module io \
    --param input:int16_t[] \
    --out-param output:float[] \
    --param n:size_t
```

`--param` declares input arrays (auto `const`-qualified) and scalar
params; `--out-param` declares writable output arrays (`const`
dropped). This scaffolds the function with a blank `<<IMPLEMENT>>` stub,
shown below; see [What you fill in](#what-you-fill-in) for the body.

Already have the implementation in another file? Add
`--impl file::funcname` (e.g. `--impl /tmp/impl.c::q15_to_float`) to the
command above and it lifts that function's body directly into the
generated file instead of leaving a stub — no separate fill-in step. The
function's own `native/src/<mod>/<fn>.c` (or `<mod>_core.c` under
`jm module --functions-in-core`) is a sacred file — once written,
`jm apply` never overwrites it — so lifting a real body in is safe and has
no splice-into-existing-file hazard.

`--impl` also takes a **line range**: `--impl file::N:M` lifts lines
`N..M` (inclusive, 1-based) verbatim instead of a named body — handy
when the source isn't a clean standalone function. Out-of-bounds or
inverted ranges error cleanly. `--replace old::new` applies string
substitutions to the lifted text before injection.

## What you get

### `native/inc/my_dsp/io/io_core.h` (declaration)

```c
void my_dsp_q15_to_float(const int16_t *input,  size_t input_len,
                         float         *output, size_t output_len,
                         size_t n);
```

`input` is `const`; `output` is not. The header and implementation
always match. The C symbol carries `[project] c_prefix` (`my_dsp_`, the
package name `jm new` defaults it to); the Python name does not.

### `native/src/io/q15_to_float.c` (stub — the function's own sacred file)

```c
/* <<IMPLEMENT: my_dsp_q15_to_float>> */
void
my_dsp_q15_to_float(const int16_t *input,  size_t input_len,
                    float         *output, size_t output_len,
                    size_t n)
{
    (void)input; (void)input_len;
    (void)output; (void)output_len;
    (void)n;
}
```

The Python binding (`io_ext.c`) auto-generates: numpy-array acquisition
for `input` (read-only, C-contiguous), validation of the caller's
writable `output` array, and the scalar parsing for `n`. To have the
function allocate and return its output instead, use `--out-type` (see
[Variants](#variants)).

## What you fill in

The function body. For Q15 → float that's two lines:

```c
void
my_dsp_q15_to_float(const int16_t *input,  size_t input_len,
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
q15_to_float(inp, out, inp.size)               # positional
q15_to_float(input=inp, output=out, n=inp.size)  # or by keyword
```

Generated functions are **positional-or-keyword**: each parameter can be passed
positionally or by name. Keyword *capability* is essentially free; you only pay
the keyword-matching cost (~12–25 ns/arg) when you actually pass by name. See
[Arguments: positional vs keyword](../arguments.md) for the full cost model and
the project-wide rule (the per-sample `step()`/`steps()` hot path stays
positional-only).

## Variants

- **Multiple outputs** — repeat `--out-param`.
- **Inline (header-only)** — pass `--inline` to emit a `static inline`
    body in `_core.h` so the function inlines at every call site. Good
    for short, pure functions.
- **`--out-type T`** — the function allocates and returns a fresh `T[]`
    ndarray instead of writing through an `--out-param`, sized from the
    first array param's length. `--out-type 'T[n]'` sizes it from the
    integer param `n` instead: `jm function make_window --module win --param n:size_t --out-type 'float[n]'` generates `void my_dsp_make_window(float *out, size_t n)`, called from Python as `make_window(512)`. With neither, the function is refused: the C body is never told the length, so jm will not guess it (gh-1888).
- **`--result-field name:type`** — emit a list of `{name, type}` records
    per call (repeatable). Requires `--return-type <struct>` naming the C
    struct one row is, declared in the sacred header.

## Concrete types

| Slot                   | Accepts                                                                                                                                                                                                                                                                                                            | Rejects                                                  | Default          |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------- | ---------------- |
| `--param name:T`       | Any [scalar](../types.md#module-function-param-types) or any `T[]` [array shape](../types.md#array-element-types). Arrays get `const`. Also `name:path` (`str \| os.PathLike`, C `const char *`) and `name:enum:<name>` (a declared `[[enum]]`) — see [`jm function`](../commands/extend.md#just-makeit-function). | `const char *`, `T[][]`, `string_enum:…` (object-only).  | `n:size_t`       |
| `--out-param name:T[]` | Array shapes only. Drops `const`.                                                                                                                                                                                                                                                                                  | All scalars (rejected at parse time per gh-72), `T[][]`. | `output:float[]` |
| `--return-type T`      | Any [scalar](../types.md#module-function-param-types) including `void`.                                                                                                                                                                                                                                            | `const char *`, any `T[]`.                               | `void`           |
| `--out-type T`         | Any [array element type](../types.md#array-element-types). Sizes the returned ndarray from the first array param's length, or from the integer param `n` named by `T[n]`. With neither it is refused (gh-1888).                                                                                                    | `bool`, `int`, `const char *`, `long double _Complex`.   | —                |

The function preset has the **narrowest** slot allowlist of any
template — no free-form strings (a filesystem path is `name:path`), no
inline `string_enum:` (a declared `[[enum]]` is `name:enum:<name>`), no
2-D arrays. Need those? Wrap the logic in an object preset instead.
