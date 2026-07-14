# Types and patterns

This page has two halves. The first is a tight **quick reference** —
arrays, pointers (including `void`), opaque state fields, the full
supported-types table, and the common patterns they combine into.
The second is the per-slot detail, useful when a flag rejects a type
and you want to know exactly which slot's allowlist drove the
rejection.

If a type isn't listed under a slot below, that flag will reject it.
This page is the single source of truth.

______________________________________________________________________

## Quick reference

### Arrays — two shapes for two jobs

| Shape  | Lives                            | Crosses Python?                | Declared as                                                        |
| ------ | -------------------------------- | ------------------------------ | ------------------------------------------------------------------ |
| `T[]`  | A step / function / method param | yes — becomes a numpy ndarray  | `--arg-type "T[]"`, `--param "name:T[]"`, `--out-param "name:T[]"` |
| `T[N]` | Inside a state struct            | no — C-only, embedded directly | `--state "name:T[N]"`                                              |

`T[]` is the variable-length form parsed from a numpy array and
passed to C as `const T *` (or `T *` for an `--out-param`).

`T[N]` is a fixed-length C array embedded in the state struct — one
allocation for the whole object, no pointer chasing, no separate
free. There is **no auto getter/setter for fixed-length state arrays**;
access them in C as `state->name[i]`.

The element type `T` must be one of the
[array-element types](#array-element-types) — a strict subset of the
scalar registry (excludes `bool`, `int`, `const char *`,
`long double _Complex`).

### Pointers — three forms in generated code

| Pointer     | Where it appears                                                                                            |
| ----------- | ----------------------------------------------------------------------------------------------------------- |
| `const T *` | Default for an array parameter — the function promises not to write.                                        |
| `T *`       | Output array parameter (`--out-param`) — `const` is dropped so the function can write into the buffer.      |
| `void *`    | Inside opaque state, or as a vendor handle whose definition stays in C. Rarely appears on a Python surface. |

`const` semantics are load-bearing. A `T[]` declared without
`--out-param` is `const T *` in C, and the binding will not let you
write through the buffer from Python. Use `--out-param` for writable
output buffers.

### Opaque state fields

Some types don't fit the scalar registry — heap pointers to your own
structs, `FILE *`, third-party plans (`fftwf_plan`,
`pocketfft_plan_t`), or any C type with no canonical Python
representation. Declare these as **opaque**: the renderer emits the
type verbatim and generates no auto-machinery for the field. You own
the lifecycle — you write the `create()` / `destroy()` /
optionally `reset()` bodies directly in `_core.c`.

When a state field is opaque, the renderer:

- Emits the type into the struct verbatim.
- Generates **no auto-getter, setter, ctor kwarg, or reset assignment**.
- Leaves `/* TODO */` markers in the lifecycle bodies so you can fill
    them in.

This is the escape hatch for types just-makeit can't introspect. You
take over; the renderer steps aside.

> **CLI parity gap.** A `--state name:type:opaque` flag is pending.
> Today, marking a state field as opaque requires one line in
> `just-makeit.toml` (`opaque = true` on the entry). Because `_core.c`
> is sacred — `jm apply` never overwrites it — your hand-written
> lifecycle bodies survive every later edit. See the
> [vendor plan pattern](#vendor-plan-in-opaque-state) below for the
> full recipe.

### Supported types

Every type registered in `_CTYPE_META`, plus the array shapes and
opaque. Slot legality:

- **S** — state field (`--state`)
- **IO** — step input / output (`--arg-type`, `--return-type`)
- **I** — init param (`--init-param`)
- **P** — function / method param (`--param`)
- **A** — array element (legal as `T` in `T[]` or `T[N]`)

| C type                 | NumPy dtype      | S   | IO  | I   | P   | A   | Zero literal          |
| ---------------------- | ---------------- | --- | --- | --- | --- | --- | --------------------- |
| `float`                | `np.float32`     | ✓   | ✓   | ✓   | ✓   | ✓   | `0.0f`                |
| `double`               | `np.float64`     | ✓   | ✓   | ✓   | ✓   | ✓   | `0.0`                 |
| `int`                  | `np.int32`       | ✓   | ✓   | ✓   | ✓   |     | `0`                   |
| `bool`                 | `np.bool_`       | ✓   | ✓   | ✓   | ✓   |     | `0`                   |
| `int8_t`               | `np.int8`        | ✓   | ✓   | ✓   | ✓   | ✓   | `0`                   |
| `int16_t`              | `np.int16`       | ✓   | ✓   | ✓   | ✓   | ✓   | `0`                   |
| `int32_t`              | `np.int32`       | ✓   | ✓   | ✓   | ✓   | ✓   | `0L`                  |
| `int64_t`              | `np.int64`       | ✓   | ✓   | ✓   | ✓   | ✓   | `0LL`                 |
| `uint8_t`              | `np.uint8`       | ✓   | ✓   | ✓   | ✓   | ✓   | `0U`                  |
| `uint16_t`             | `np.uint16`      | ✓   | ✓   | ✓   | ✓   | ✓   | `0U`                  |
| `uint32_t`             | `np.uint32`      | ✓   | ✓   | ✓   | ✓   | ✓   | `0UL`                 |
| `uint64_t`             | `np.uint64`      | ✓   | ✓   | ✓   | ✓   | ✓   | `0ULL`                |
| `size_t`               | `np.uintp`       | ✓   | ✓   | ✓   | ✓   | ✓   | `0ULL` (parsed)       |
| `ptrdiff_t`            | `np.intp`        | ✓   | ✓   | ✓   | ✓   | ✓   | `0LL` (parsed)        |
| `float _Complex`       | `np.complex64`   | ✓   | ✓   | ✓   | ✓   | ✓   | `0.0f + 0.0f * I`     |
| `double _Complex`      | `np.complex128`  | ✓   | ✓   | ✓   | ✓   | ✓   | `0.0 + 0.0 * I`       |
| `long double _Complex` | `np.clongdouble` | ✓   | ✓   | ✓   | ✓   |     | `0.0L + 0.0L * I`     |
| `const char *`         | `str`            |     |     | ✓   |     |     | `NULL`                |
| `void`                 | (none)           |     | ✓   |     |     |     | — (`--arg-type void`) |
| `T[N]` (fixed array)   | (none — C-only)  | ✓   |     |     |     |     | `{0}`                 |
| `T[]` (variable array) | (T's dtype)      |     | ✓   | ✓   | ✓   |     | numpy-owned           |
| *opaque* (declared)    | (none)           | ✓   |     |     |     |     | user-managed          |

Notes:

- `bool` and `int` are not array element types: `bool` doesn't fit
    the numpy parse path (use `uint8_t` for byte arrays); `int` has
    platform-dependent width (use `int32_t`).
- `const char *` is only legal as an init-param — strings can't be
    state fields (no lifetime story) or step inputs (no per-sample
    semantics). If you need a string in state, declare an opaque
    field and copy / strdup it in your `_core.c` `create()` body.
- `long double _Complex` is truncated to `double _Complex` at the
    Python boundary; not legal as an array element (no contiguous
    numpy dtype).
- `void` is special — only legal as `--arg-type` or `--return-type`,
    where it strips that side of the step signature
    ([generator](templates/generator.md), [consumer](templates/consumer.md)).
- `bool` is a usable scalar everywhere a scalar is legal (state, step
    IO, init-param, function param) — it just isn't an *array element*
    type (use `uint8_t` for byte arrays).
- Array **input** (`T[]` as `--arg-type`, `--param`, `--out-param`)
    works. Array **return** (`--return-type "T[]"`) is supported via
    `--preset blockwise` (array-in / array-out; see
    [blockwise](templates/blockwise.md)).

### Patterns

Four common combinations of the above. Each is a worked recipe you
can paste verbatim.

#### Fixed coefficient table inside state

```sh
jm object my_fir --state "coeffs:float[64]" --state "n_taps:uint8_t:0"
```

`coeffs[64]` lives in the struct; one alloc; populate inside
`my_fir_create()` in `_core.c`, or via a custom setter method. Access
in C as `state->coeffs[i]`.

#### Vendor plan in opaque state

```sh
jm object my_fft \
    --arg-type "float _Complex[]" \
    --init-param n:size_t
# then declare the opaque field — CLI flag pending:
#   --state plan:fftwf_plan:opaque
# Until that lands, set `opaque = true` on the [[state]] entry once.
```

Renderer treats `plan` as a black box. You call `fftwf_plan_dft_1d()`
inside `my_fft_create()` and `fftwf_destroy_plan()` inside
`my_fft_destroy()`, both in `_core.c`. The state struct carries
`fftwf_plan plan;` verbatim.

#### File / socket reader

```sh
jm object iq_reader --no-step \
    --init-param filepath:"const char *" \
    --state fd:int:-1 \
    --state file_size:size_t:0
```

`filepath` is the user-facing ctor arg (parsed as Python `str`).
`fd` lives in state, initialised inside `iq_reader_create()` after
`open()`.

#### Function with output buffer

```sh
jm function q15_to_float --module io \
    --param input:int16_t[] \
    --out-param output:float[] \
    --param n:size_t
```

`input` is `const int16_t *`; `output` is writable `float *` (no
`const`). Python side: `q15_to_float(input_arr, output_arr, n)` —
caller passes both buffers.

!!! warning "Record-returning methods/functions (`--result-field`)"

    `--result-field` (an event emitter returning a list or single record
    of a user-defined C struct — see [`jm method`](commands/extend.md))
    currently has known codegen gaps where the generated `_core.h`
    declaration and the `_core.c` stub disagree on the C signature,
    so a freshly scaffolded record-returning method/function does not
    always compile as-is. If you hit a "conflicting types" error here,
    that's why — check the generated header against the stub and
    reconcile the signature by hand before filling in the body.

______________________________________________________________________

## Type slots — per-slot detail

| Slot                                                      | CLI flags                                | TOML field                       |
| --------------------------------------------------------- | ---------------------------------------- | -------------------------------- |
| [State variable](#state-variable-types)                   | `--state name:T:D`                       | `[[obj.state]] type = "T"`       |
| [Step input / output](#step-input-output-types)           | `--arg-type T`, `--return-type T`        | `arg_type`, `return_type`        |
| [Constructor / init param](#constructor-init-param-types) | `--init-param name:T[:D]`                | `[[obj.init_params]] type = "T"` |
| [Module function param](#module-function-param-types)     | `--param name:T`, `--out-param name:T[]` | `[[fn.params]] type = "T"`       |
| [Method param](#method-param-types)                       | *(TOML only today)*                      | `[[method.params]] type = "T"`   |

Templates in the [gallery](templates/index.md) list their concrete type
choices per slot at the bottom of each page, with links back into the
sections below.

______________________________________________________________________

## State variable types

State variables are declared with `--state name:type[:default]`.

The type determines the C struct field, the `PyArg_ParseTuple` format code, the
NumPy dtype in the generated stub, and the default zero value used when no
default is supplied.

## Supported types

### Floating point

| Type     | C field type | NumPy type   | Format | Zero literal |
| -------- | ------------ | ------------ | ------ | ------------ |
| `float`  | `float`      | `np.float32` | `f`    | `0.0f`       |
| `double` | `double`     | `np.float64` | `d`    | `0.0`        |

### Integer

| Type        | C field type | NumPy type  | Zero literal |
| ----------- | ------------ | ----------- | ------------ |
| `int`       | `int`        | `np.int32`  | `0`          |
| `int8_t`    | `int8_t`     | `np.int8`   | `0`          |
| `int16_t`   | `int16_t`    | `np.int16`  | `0`          |
| `int32_t`   | `int32_t`    | `np.int32`  | `0`          |
| `int64_t`   | `int64_t`    | `np.int64`  | `0`          |
| `uint8_t`   | `uint8_t`    | `np.uint8`  | `0U`         |
| `uint16_t`  | `uint16_t`   | `np.uint16` | `0U`         |
| `uint32_t`  | `uint32_t`   | `np.uint32` | `0U`         |
| `uint64_t`  | `uint64_t`   | `np.uint64` | `0U`         |
| `size_t`    | `size_t`     | `np.uintp`  | `0`          |
| `ptrdiff_t` | `ptrdiff_t`  | `np.intp`   | `0`          |

Fixed-width types require `<stdint.h>`, which is included via `clib_common.h`.
They are parsed through the nearest standard integer type and cast to the
declared type in the generated extension.

`size_t` and `ptrdiff_t` are pointer-sized types useful for lengths, offsets,
and index arithmetic. They map to NumPy's `uintp` and `intp` respectively.

`int` is kept for convenience; prefer `int32_t` when bit-width matters.

### Complex

| Type                   | C field type           | NumPy type       | Zero literal      |
| ---------------------- | ---------------------- | ---------------- | ----------------- |
| `float _Complex`       | `float _Complex`       | `np.complex64`   | `0.0f + 0.0f * I` |
| `double _Complex`      | `double _Complex`      | `np.complex128`  | `0.0 + 0.0 * I`   |
| `long double _Complex` | `long double _Complex` | `np.clongdouble` | `0.0L + 0.0L * I` |

Complex types are parsed via `Py_complex` (CPython format `"D"`) and cast to
the target C type. `long double _Complex` is truncated to `double` at the
Python boundary.

### Fixed-length arrays

Append `[N]` to any scalar type to embed a fixed-length C array directly inside
the state struct. `N` must be a positive integer literal.

```sh
--state "coeffs:float[16]"            # float coeffs[16];
--state "delay:float _Complex[16]"    # float _Complex delay[16];
--state "history:double[64]"          # double history[64];
```

The array lives **inside** the struct — one `malloc` for the whole object, no
pointer chasing, no separate free. This is the right choice for fixed-size
delay lines, coefficient tables, and circular buffers whose length is known at
code-generation time.

Array fields do not support explicit defaults — they are always
zero-initialized at construction. There are no auto-generated getter/setter
methods for array fields; access them directly in your C implementation via
`state->coeffs[i]`.

Array fields work with `--state` (standalone objects and `object --module`)
and are recorded verbatim in `just-makeit.toml`, so `jm add` and `jm config`
round-trip them correctly.

### Opaque state fields (pointers, handles)

For heap pointers, file handles, FFTW plans, or any C type that doesn't
fit `_CTYPE_META`, declare the field as `opaque = true` (currently
TOML-only; `--state name:type:opaque` flag pending). The type string
is emitted into the struct verbatim and no auto-getter, setter, kwarg,
or reset assignment is generated — lifecycle is your responsibility,
written directly in `_core.c`'s `create()` / `destroy()` (and
optionally `reset()`) bodies. See the
[Quick reference opaque section](#opaque-state-fields) above for the
short version and the [vendor plan pattern](#vendor-plan-in-opaque-state)
for a worked example.

______________________________________________________________________

## Defaults

If you omit the default, the zero literal for the declared type is used:

```sh
--state gain:double        # 0.0
--state count:uint8_t      # 0U
--state "pole:double _Complex"  # 0.0 + 0.0 * I
```

Explicit defaults must be valid C literals for the type:

```sh
--state gain:double:1.0
--state order:int32_t:4
--state mask:uint8_t:255
```

> **Note:** Custom defaults for complex types are not supported via the CLI.
> Complex state always initialises to zero; set a non-zero default directly in
> the generated `_core.c` after scaffolding.

## C to NumPy mapping

Getters return the exact NumPy scalar for the declared C type; setters accept
the same type:

```c
double engine_get_gain(const engine_state_t *state);
void   engine_set_gain(engine_state_t *state, double val);

uint8_t engine_get_channel(const engine_state_t *state);
void    engine_set_channel(engine_state_t *state, uint8_t val);
```

```python
def get_gain(self) -> np.float64: ...
def set_gain(self, value: np.float64) -> None: ...

def get_channel(self) -> np.uint8: ...
def set_channel(self, value: np.uint8) -> None: ...
```

## Notes

- All state variables appear as optional keyword arguments to `__init__` —
    `Component()` with no arguments is always valid.
- `reset()` restores every field to its declared default, not the zero literal.
- The C struct is opaque — always access fields through the generated
    getter/setter API.

______________________________________________________________________

## Step input / output types

The `--arg-type` and `--return-type` flags set the C signature of `<comp>_step`
and `<comp>_steps`. Both accept the same allowlist plus a few shape forms.

### Scalar shapes

Every type in [State variable types](#state-variable-types) except
`const char *` is also a legal `--arg-type` / `--return-type` value.
Strings can't flow through a sample-by-sample DSP step.

### Array shape — `T[]`

Append `[]` to any element type from the
[array dtypes](#array-element-types) table to declare an input array
parameter that arrives as a numpy ndarray and expands to
`(const T *name, size_t name_len)` in C.

```sh
jm object xform --arg-type "float[]" --return-type "float[]"
```

### The `void` shape

Pass `void` to either flag to omit that side of the signature:

| Combination                       | What it produces                     | Preset                              |
| --------------------------------- | ------------------------------------ | ----------------------------------- |
| `--arg-type void --return-type T` | Generator: `step()` takes no input.  | [generator](templates/generator.md) |
| `--arg-type T --return-type void` | Consumer: `step()` returns nothing.  | [consumer](templates/consumer.md)   |
| `--arg-type void` + `--no-step`   | Custom verbs only; no auto `step()`. | [reader](templates/reader.md)       |

### Element types accepted in the array form { #array-element-types }

The element-type set is a strict subset of `_CTYPE_META` — `bool`,
`int`, `const char *`, and `long double _Complex` are not legal array
elements (no canonical numpy dtype).

| `T[]` form          | C element         | NumPy dtype     |
| ------------------- | ----------------- | --------------- |
| `float[]`           | `float`           | `np.float32`    |
| `double[]`          | `double`          | `np.float64`    |
| `float _Complex[]`  | `float _Complex`  | `np.complex64`  |
| `double _Complex[]` | `double _Complex` | `np.complex128` |
| `int8_t[]`          | `int8_t`          | `np.int8`       |
| `int16_t[]`         | `int16_t`         | `np.int16`      |
| `int32_t[]`         | `int32_t`         | `np.int32`      |
| `int64_t[]`         | `int64_t`         | `np.int64`      |
| `uint8_t[]`         | `uint8_t`         | `np.uint8`      |
| `uint16_t[]`        | `uint16_t`        | `np.uint16`     |
| `uint32_t[]`        | `uint32_t`        | `np.uint32`     |
| `uint64_t[]`        | `uint64_t`        | `np.uint64`     |
| `size_t[]`          | `size_t`          | `np.uintp`      |
| `ptrdiff_t[]`       | `ptrdiff_t`       | `np.intp`       |

______________________________________________________________________

## Constructor / init-param types

Constructor parameters are the broadest slot. They feed `<comp>_create`
and the Python `__init__`, and they need to accept things the DSP hot
path doesn't — filepaths, format names, optional buffers.

| Type form                                                    | Use case                                    | Example                                         |
| ------------------------------------------------------------ | ------------------------------------------- | ----------------------------------------------- |
| Any [scalar](#state-variable-types) including `const char *` | flags, options, paths                       | `--init-param filepath:"const char *"`          |
| Any [array shape](#array-element-types) `T[]`                | required positional ndarray                 | `--init-param coeffs:"float _Complex[]"`        |
| `T[][]` (2-D array)                                          | required 2-D ndarray (e.g. polyphase banks) | `--init-param bank:"float _Complex[][]"`        |
| `string_enum:a,b,c`                                          | optional string mapped to a C enum index    | `--init-param mode:"string_enum:read,write,rw"` |
| `enum:<name>`                                                | a named `[[enum]]` (single source of truth) | `--init-param mode:"enum:io_mode"`              |
| `T[N]` (fixed length)                                        | not accepted here — use `--state` for that  | —                                               |

`const char *` is legal as an init-param but **not** as a state field —
strings live in Python land or the caller's memory; the state struct
holds the parsed/converted result. The reader template carries
`filepath:"const char *"` in its init-params and `fd:int` in its state.

### Named enums — `[[enum]]` single source of truth

`string_enum:a,b,c` inlines the choices on the parameter. When the *same*
value set is used by more than one parameter — or needs to feed more than the
one binding (a CLI choice flag, a JSON field, a C enum) — inlining duplicates
it, and the copies drift. Declare it once at the top level instead, then refer
to it with `enum:<name>`:

```toml
[[enum]]
name = "io_mode"
values = ["read", "write", "rw"]   # order IS the C int — append-only

[[reader.init_params]]
name = "mode"
type = "enum:io_mode"
```

`enum:<name>` resolves to the equivalent `string_enum:read,write,rw` on the
codegen read path, so it behaves identically everywhere `string_enum:` does
(choice flags, stubs, the C enum index) — the manifest just keeps the value
list in one place. Value order is the C integer value, so **append only; never
reorder**. Referring to an undeclared enum is an error. (Requires schema 7;
run `jm upgrade`.)

______________________________________________________________________

## Module-function param types

Module-level functions (`jm function FN --module MOD`) accept the
narrowest slot — no string enums, no 2-D arrays.

| Param flag             | Legal types                                                                                                                                |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `--param name:T`       | Any [scalar](#state-variable-types) except `const char *`, or any `T[]` [array shape](#array-element-types). Arrays are `const`-qualified. |
| `--out-param name:T[]` | Array shapes **only**. Drops `const`. Rejected for scalars (gh-72).                                                                        |

The whole-function `--out-type T` flag (currently TOML only) makes the
function return a fresh ndarray sized from the first array param's
length, or — when no array param is present — from the first integer
scalar param (gh-65).

______________________________________________________________________

## Method param types

Methods on stateful objects (`jm method OBJ METHOD`) accept the same
set as module-function params (`--param` plus `--out-param` semantics),
extended with three TOML-only knobs that don't yet have CLI flags:

| TOML field                          | Effect                                                                                                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `variable_output = true`            | Method returns up to `<comp>_<verb>_max_out()` samples; the binding pre-allocates the buffer once.                                                           |
| `out_type = "T"`                    | The method writes a fresh `T[]` buffer sized from an array param length (or a scalar integer param, per gh-65).                                              |
| `result_fields = [{name, type}, …]` | The method emits a list of records; each tuple becomes a row in the returned list. Field types follow the [state variable](#state-variable-types) allowlist. |

______________________________________________________________________

## Sacred vs glue files

The manifest (`just-makeit.toml`) is the source of truth, but not every
generated file is rewritten the same way when you re-run `jm apply`:

- **Glue — regenerated every apply.** `<comp>_ext.c`,
    `src/<pkg>/<comp>.pyi`, and `CMakeLists.txt` are derived purely
    from the manifest. Edit the TOML and they refresh on the next apply.
- **`<comp>_core.h` — mixed.** `apply` injects a missing method/property
    *declaration*, but the inline `step()` body and the state struct are
    **sacred** — never re-rendered. A new state field reaches the struct via a
    rebuild, not `apply`.
- **`<comp>_core.c` — sacred.** Once it exists, `jm apply` never splices
    or re-renders it; the `steps()` / lifecycle bodies are yours.

So editing the manifest propagates to glue and injects missing declarations,
but a signature change or a new state field is *structural* — use
`jm regenerate` (or `jm add` for state) to rebuild the sacred body. A new
method or computed property is additive: `jm method` / `jm property` inject a
declaration and append a fresh stub.

`jm regenerate <component>` is the deliberate-refresh half: it deletes
every file the component owns and re-runs `jm apply`, rebuilding a clean
scaffold from the manifest (the manifest itself is untouched, unlike
`jm remove`). It discards hand-written `_core.c` bodies, so `git stash`
first. `--force` skips the single confirmation. Works for both
standalone and module objects.

______________________________________________________________________

## See also

- [Template gallery](templates/index.md) — each preset declares its
    slot allowlist concretely at the bottom of its page.
- [doppler — Type System](https://doppler-dsp.github.io/doppler/types/) —
    how doppler uses these C types in its DSP APIs (CF32, CF64, integer IQ
    pairs, `dp_sample_type_t`).
