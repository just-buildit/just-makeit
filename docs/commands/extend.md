# Extend commands

These commands add behaviour to objects and modules that already exist in
`just-makeit.toml`.  Run them from the project root after scaffolding.

______________________________________________________________________

## `just-makeit method <object> <method_name> [--module name] [--param name:type ...] --return-type TYPE [--variable-output] [--arg-type TYPE] [--multi-output TYPE ...]`

Add a named execute method to an existing object.

Each method appends a C stub to `<obj>_core.c` and regenerates the module
`_ext.c` with the new Python glue.

**Arguments**

| Argument                | Description                                                                                                                                                                                                                                                                                                                      |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`                | Object name (must already exist in `just-makeit.toml`).                                                                                                                                                                                                                                                                          |
| `method_name`           | Snake-case name for the new method.                                                                                                                                                                                                                                                                                              |
| `--module name`         | Module the object belongs to (required for module objects).                                                                                                                                                                                                                                                                      |
| `--param name:type`     | Named typed scalar parameter. Repeatable.                                                                                                                                                                                                                                                                                        |
| `--param name:type[]`   | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C.                                                                                                                                                                                                                                   |
| `--return-type TYPE`    | C type of the return value (`void` for no return).                                                                                                                                                                                                                                                                               |
| `--arg-type TYPE`       | C type of a single array-style input. Use `void` for count-only inputs.                                                                                                                                                                                                                                                          |
| `--variable-output`     | Pre-allocate output buffer at init; return zero-copy numpy view. See below.                                                                                                                                                                                                                                                      |
| `--multi-output TYPE`   | Add a second (or further) output array. Repeatable; produces a tuple return.                                                                                                                                                                                                                                                     |
| `--out-type TYPE`       | Allocate a `complex64` (or other) output array per call and pass `*out` to C. The C stub receives `(... , elem_t *out)` and the Python wrapper allocates and returns the ndarray automatically. The output length equals `in_len / out_divisor`.                                                                                 |
| `--out-divisor N`       | Divide the input length by `N` to determine the output array length when `--out-type` is active (default: 1). Use `2` for methods that interpret the input as interleaved I/Q pairs (e.g. a CI8 buffer where each complex sample is 2 bytes).                                                                                    |
| `--batch`               | Generate a 1:1-rate array transform. The C stub receives `(state, const in_t *in, size_t n, out_t *out)` (or `(state, size_t n, out_t *out)` for `--arg-type void`). The Python wrapper allocates an output array of length `n` per call and returns it. Use when output length equals input length and is unknown at init time. |
| `--impl file::funcname` | Lift the method body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub.                                                                                                                                                                                                                                 |
| `--replace old::new`    | String substitution applied to the body lifted by `--impl`. Repeatable.                                                                                                                                                                                                                                                          |

______________________________________________________________________

### Named parameters (`--param`)

Use `--param name:type` when the method takes multiple distinct typed scalar
inputs.  This generates named parameters in both the C stub and the Python
wrapper.

```sh
just-makeit method nco configure --module dsp \
    --param freq:float \
    --param phase:float \
    --param mode:int32_t \
    --return-type void
```

Generated C stub:

```c
void
nco_configure(nco_state_t *state, float freq, float phase, int32_t mode)
{
    (void)state; (void)freq; (void)phase; (void)mode;
}
```

Python call:

```python
nco.configure(0.1, 0.0, 2)
```

All scalar types in `_CTYPE_META` are supported as `--param` types (float,
double, int, int32_t, uint32_t, size_t, float \_Complex, etc.).

**Array parameters** (`--param name:type[]`) generate a numpy array input.
The C stub receives `(const elem_t *name, size_t name_len)` and the Python
wrapper performs `PyArray_FROM_OTF` automatically:

```sh
just-makeit method resamp execute_ctrl --module resample \
    --param ctrl:"float _Complex[]" \
    --return-type size_t
```

Generated C stub:

```c
size_t
resamp_execute_ctrl(resamp_state_t *state,
                    const float complex *ctrl, size_t ctrl_len)
{
    (void)state; (void)ctrl; (void)ctrl_len;
    return (size_t)0;
}
```

Python call: `resamp.execute_ctrl(np.zeros(64, dtype=np.complex64))`

`--param` and `--arg-type` are mutually exclusive per method.

______________________________________________________________________

### Output modes

Choosing the right output mode depends on whether the **maximum output count
is knowable at init time**.

______________________________________________________________________

#### `--batch` — 1:1-rate array transform

Use `--batch` when output length equals input length and is unknown at init
time.  The generated C stub receives `(state, const in_t *in, size_t n,
out_t *out)` and the Python wrapper allocates and returns an output array of
length `n` per call.

```sh
just-makeit method nco steps_u32 --module source \
    --arg-type void --return-type uint32_t --batch

just-makeit method nco steps_ctrl --module source \
    --arg-type float --return-type float --batch
```

Generated C stubs:

```c
void nco_steps_u32(nco_state_t *state, size_t n, uint32_t *out);
void nco_steps_ctrl(nco_state_t *state, const float *in, size_t n, float *out);
```

Python calls:

```python
ph  = nco.steps_u32(1024)    # returns uint32 ndarray of length 1024
out = nco.steps_ctrl(ctrl)   # ctrl is float32 ndarray; returns float32 ndarray
```

______________________________________________________________________

#### `--variable-output` — pre-allocated zero-copy batch

Use `--variable-output` when the **maximum output count is bounded by the
object's state and knowable at init time** (decimators, FIFOs with fixed
capacity).  The generated code calls `<method>_max_out(state)` at init time,
pre-allocates a fixed output buffer, and returns a **zero-copy numpy view**
into that buffer on every call — no per-call `malloc`.

```sh
just-makeit method hbdecim execute --module resample \
    --arg-type "float _Complex" --return-type "float _Complex" \
    --variable-output
```

Generated C stubs:

```c
/* Return maximum output samples possible given current state. */
size_t hbdecim_execute_max_out(hbdecim_state_t *state);

/* Process n_in samples; write up to _max_out results; return actual count. */
size_t hbdecim_execute(hbdecim_state_t *state,
                       const float complex *in, size_t n_in,
                       float complex *out);
```

Python call:

```python
out = decim.execute(block)   # zero-copy view; valid until next call
```

| Use case                                   | `_max_out` returns | Use `--variable-output`? |
| ------------------------------------------ | ------------------ | ------------------------ |
| Decimator, fixed ratio `R`, block size `B` | `ceil(B / R)`      | Yes                      |
| Buffer / FIFO with fixed capacity `C`      | `C`                | Yes                      |
| FIR filter, 1:1 rate                       | unknown at init    | No — use `--batch`       |
| NCO extended outputs, 1:1 rate             | unknown at init    | No — use `--batch`       |

**Warning:** if `_max_out` returns 0 (the placeholder default), `malloc(0)`
behaviour is implementation-defined.  Always implement `_max_out` before
calling the method from Python.

______________________________________________________________________

#### `--multi-output`

Each `--multi-output TYPE` adds a parallel output array, producing a tuple
return.  Combine with `--variable-output`:

```sh
just-makeit method nco steps_u32_ovf --module resample \
    --arg-type void --return-type uint32_t \
    --variable-output --multi-output uint8_t
```

______________________________________________________________________

## `just-makeit property <object> <prop_name> [--module name] --type TYPE [--writable] [--field]`

Add a read-only (or read-write) Python property to an existing object.

```sh
just-makeit property nco phase --module source --type uint32_t
just-makeit property nco phase_inc --module source --type uint32_t
just-makeit property buffer capacity --type size_t --writable
just-makeit property reader samples_read --module conv --type uint32_t --field
```

Generates a `get_<prop>()` C function stub (and `set_<prop>()` if `--writable`)
that you implement in `<obj>_core.c`, plus the Python getter (and setter) glue
in the module `_ext.c`.

**Arguments**

| Argument        | Description                                                                                                                                                                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`        | Object name (must already exist in `just-makeit.toml`).                                                                                                                                                                                                 |
| `prop_name`     | Snake-case property name.                                                                                                                                                                                                                               |
| `--module name` | Module the object belongs to (required for module objects).                                                                                                                                                                                             |
| `--type TYPE`   | C type of the property value.                                                                                                                                                                                                                           |
| `--writable`    | Also generate a setter. Without this flag the property is read-only.                                                                                                                                                                                    |
| `--field`       | Add a `TYPE prop_name;` field to the state struct and auto-implement the getter as `return state->prop_name`. No `<<IMPLEMENT>>` stub is generated — the field is the implementation. Combine with `--writable` for a read-write struct field property. |

______________________________________________________________________

## `just-makeit function <name> --module <mod> [--param name:type ...] [--return-type TYPE] [--doc "text"]`

Add a stateless C function to an existing module — no struct, no lifecycle,
no persistent state.

Appends a C stub to `native/src/<module>/<module>_core.c` (never regenerated
— your implementation is safe) and injects the declaration into
`native/inc/<module>/<module>_core.h`.  Then regenerates `<module>_ext.c` to
add a `_bind_<name>` Python wrapper and wire it into the `PyMethodDef` array.

**Arguments**

| Argument                | Description                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| `name`                  | Snake-case function name.                                                                          |
| `--module mod`          | Module the function belongs to (required).                                                         |
| `--param name:type`     | Named typed scalar parameter. Repeatable.                                                          |
| `--param name:type[]`   | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C.     |
| `--return-type TYPE`    | C return type (default: `void`).                                                                   |
| `--doc "text"`          | Python docstring for the function.                                                                 |
| `--impl file::funcname` | Lift the function body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub. |
| `--replace old::new`    | String substitution applied to the body lifted by `--impl`. Repeatable.                            |

**Example — no parameters:**

```sh
just-makeit function fft_global_setup --module fft --doc "Initialize FFT tables."
```

`fft_core.c` (yours to implement):

```c
/* <<IMPLEMENT: fft_global_setup>> */
void
fft_global_setup(void)
{
}
```

`fft_core.h` (declaration injected automatically):

```c
void fft_global_setup(void);
```

**Example — with parameters:**

```sh
just-makeit function compute_window \
    --module fft \
    --param n:size_t \
    --param beta:float \
    --return-type float
```

`fft_core.c`:

```c
/* <<IMPLEMENT: compute_window>> */
float
compute_window(size_t n, float beta)
{
    (void)n; (void)beta;
    return (float)0.0f; /* placeholder */
}
```

Python call:

```python
from my_pkg import fft
w = fft.compute_window(512, 5.0)
```

**Array parameters** work identically to `jm method`: append `[]` to the type.

```sh
just-makeit function apply_window \
    --module fft \
    --param data:"float _Complex[]" \
    --return-type void
```
