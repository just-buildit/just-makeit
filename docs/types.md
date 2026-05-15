# State Variable Types

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
the state struct.  `N` must be a positive integer literal.

```sh
--state "coeffs:float[16]"            # float coeffs[16];
--state "delay:float _Complex[16]"    # float _Complex delay[16];
--state "history:double[64]"          # double history[64];
```

The array lives **inside** the struct — one `malloc` for the whole object, no
pointer chasing, no separate free.  This is the right choice for fixed-size
delay lines, coefficient tables, and circular buffers whose length is known at
code-generation time.

Array fields do not support explicit defaults — they are always
zero-initialized at construction.  There are no auto-generated getter/setter
methods for array fields; access them directly in your C implementation via
`state->coeffs[i]`.

Array fields work with `--state` (standalone objects and `object --module`)
and are recorded verbatim in `just-makeit.toml`, so `jm add` and `jm config`
round-trip them correctly.

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
void   engine_set_gain(engine_state_t *state, double gain);

uint8_t engine_get_channel(const engine_state_t *state);
void    engine_set_channel(engine_state_t *state, uint8_t channel);
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

## See also

- [doppler — Type System](https://doppler-dsp.github.io/doppler/types/) —
  how doppler uses these C types in its DSP APIs (CF32, CF64, integer IQ
  pairs, `dp_sample_type_t`).
