# Type slots

Every `jm` flag that takes a C type goes through one of five **slots**.
Each slot has its own allowlist — different flags accept different
subsets of the registry. This page is the single source of truth: if a
type isn't listed under a slot, that flag will reject it.

| Slot                                                       | CLI flags                                | TOML field                       |
| ---------------------------------------------------------- | ---------------------------------------- | -------------------------------- |
| [State variable](#state-variable-types)                    | `--state name:T:D`                       | `[[obj.state]] type = "T"`       |
| [Step input / output](#step-input--output-types)           | `--arg-type T`, `--return-type T`        | `arg_type`, `return_type`        |
| [Constructor / init param](#constructor--init-param-types) | `--init-param name:T[:D]` *(proposed)*   | `[[obj.init_params]] type = "T"` |
| [Module function param](#module-function-param-types)      | `--param name:T`, `--out-param name:T[]` | `[[fn.params]] type = "T"`       |
| [Method param](#method-param-types)                        | *(TOML only today)*                      | `[[method.params]] type = "T"`   |

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
fit `_CTYPE_META`, declare the field as `opaque = true` in TOML. The
type string is emitted into the struct verbatim and no auto-getter,
setter, kwarg, or reset assignment is generated — lifecycle is your
responsibility via `create_impl` / `destroy_impl`. See
[declarative-scaffolding.md](declarative-scaffolding.md#opaque-state-fields-pointers-and-handles)
for the full pattern.

______________________________________________________________________

## Defaults

If you omit the default, the zero literal for the declared type is used:

```sh
--state gain:double        # 0.0
--state count:uint8_t      # 0U
--state pole:double_Complex  # 0.0 + 0.0 * I
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
| `T[N]` (fixed length)                                        | not accepted here — use `--state` for that  | —                                               |

`const char *` is legal as an init-param but **not** as a state field —
strings live in Python land or the caller's memory; the state struct
holds the parsed/converted result. The reader template carries
`filepath:"const char *"` in its init-params and `fd:int` in its state.

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

## See also

- [Template gallery](templates/index.md) — each preset declares its
    slot allowlist concretely at the bottom of its page.
- [doppler — Type System](https://doppler-dsp.github.io/doppler/types/) —
    how doppler uses these C types in its DSP APIs (CF32, CF64, integer IQ
    pairs, `dp_sample_type_t`).
