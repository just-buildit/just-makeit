# Extend commands

These commands add behaviour to objects and modules that already exist in
`just-makeit.toml`. Run them from the project root after scaffolding.

______________________________________________________________________

## `just-makeit method <object> <method_name> [--module name] [--param name:type ...] --return-type TYPE [--variable-output] [--arg-type TYPE] [--multi-output TYPE ...]`

Add a named execute method to an existing object.

`jm method` is **additive and splice-free**: it injects the method's
declaration into `<obj>_core.h` and appends a fresh C stub to `<obj>_core.c`,
then regenerates the glue (`_ext.c`, `.pyi`) with the new Python binding.
Existing bodies in `_core.c` are never re-rendered — only the new stub is
appended, ready for you to implement.

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
| `--varargs`             | Generate a `*args/**kwargs` Python binding. See below. Mutually exclusive with `--arg-type`, `--param`, and `--variable-output`.                                                                                                                                                                                                 |
| `--pass-capacity`       | Emit the 5-arg `(…, out, size_t max_out)` C form for a `--variable-output` method (a bounds-checking C API receives the buffer capacity).                                                                                                                                                                                        |
| `--nogil`               | Release the GIL across the pure-C kernel of a `--variable-output` method (numpy accessors hoisted out first), so a thread-per-shard worker scales across cores. Opt-in: sound only when the object is not shared across threads concurrently (one object per stream). See below.                                                 |
| `--impl file::funcname` | Lift the method body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub.                                                                                                                                                                                                                                 |
| `--impl file::N:M`      | Lift lines `N`..`M` (inclusive, 1-based) instead of a named function body. Out-of-bounds or inverted ranges error cleanly.                                                                                                                                                                                                       |
| `--replace old::new`    | String substitution applied to the body lifted by `--impl`. Repeatable.                                                                                                                                                                                                                                                          |

______________________________________________________________________

### Named parameters (`--param`)

Use `--param name:type` when the method takes multiple distinct typed scalar
inputs. This generates named parameters in both the C stub and the Python
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

### Varargs methods (`--varargs`)

Use `--varargs` when a method needs fully flexible Python argument
parsing — for example, `configure(rate=48000, mode="fast")` where the
parameter set is open-ended or includes types that fall outside the
fixed `_CTYPE_META` table.

```sh
just-makeit method filter configure --varargs
```

**What gets generated:**

- **`native/src/<comp>/<comp>_<name>_core.c`** (sacred — never
    regenerated). A Python-aware C file compiled directly into the Python
    extension DSO (not the pure-C OBJECT library), so it may include
    `<Python.h>` freely. Contains a `PyObject *` function with an
    `<<IMPLEMENT>>` stub:

    ```c
    PyObject *
    filter_configure(PyObject *self, PyObject *args, PyObject *kwargs)
    {
        (void)self; (void)args; (void)kwargs;
        Py_RETURN_NONE;
    }
    ```

- **`<comp>_ext.c`** (regenerated). An `extern PyObject *` declaration
    pulls the symbol in from the binding file, and the `PyMethodDef` entry
    uses `METH_VARARGS | METH_KEYWORDS`:

    ```c
    extern PyObject *
    filter_configure(PyObject *, PyObject *, PyObject *);

    /* in PyMethodDef array: */
    {"configure", (PyCFunction)(void *)filter_configure,
     METH_VARARGS | METH_KEYWORDS, "configure(*args, **kwargs)."},
    ```

- **`native/src/<comp>/CMakeLists.txt`** (surgically updated).
    The binding `.c` file is spliced into the `Python3_add_library(...)`
    source list so cmake compiles it into the same DSO.

- **`.pyi` stub** (regenerated):

    ```python
    def configure(self, *args: Any, **kwargs: Any) -> Any: ...
    ```

- **`just-makeit.toml`**: `varargs = true` is recorded under
    `[[<comp>.methods]]`.

**Accessing the C state inside the binding:**

The `self` pointer is a `<Comp>Object *` (the Python object), not the raw
state struct. Cast it to reach the handle:

```c
typedef struct { PyObject_HEAD; filter_state_t *handle; } Obj;
filter_state_t *state = ((Obj *)self)->handle;
```

The comment at the top of the generated sacred file shows this cast
verbatim.

**Constraint:** `--varargs` is mutually exclusive with `--arg-type`,
`--param`, and `--variable-output`. Those flags all imply a specific
typed C signature; `--varargs` bypasses the type system entirely and
gives you raw Python argument access.

______________________________________________________________________

### Output modes

Choosing the right output mode depends on whether the **maximum output count
is knowable at init time**.

______________________________________________________________________

#### `--batch` — 1:1-rate array transform

Use `--batch` when output length equals input length and is unknown at init
time. The generated C stub receives `(state, const in_t *in, size_t n, out_t *out)` and the Python wrapper allocates and returns an output array of
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
capacity). The generated code calls `<method>_max_out(state)` at init time,
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

| Use case                                   | `_max_out` at init | Use `--variable-output`?  |
| ------------------------------------------ | ------------------ | ------------------------- |
| Decimator, fixed ratio `R`, block size `B` | `ceil(B / R)`      | Yes                       |
| Buffer / FIFO with fixed capacity `C`      | `C`                | Yes                       |
| FIR filter, output ≤ input length          | 0 (unknown)        | Yes — lazy-alloc kicks in |
| NCO extended outputs, 1:1 rate             | 0 (unknown)        | Yes — lazy-alloc kicks in |

**Lazy-alloc when `_max_out` returns 0:** if `{comp}_{method}_max_out()` returns 0
at construction (e.g. a FIR whose tap count isn't known until `_create` runs), the
output buffer is left `NULL` — no `malloc(0)` hazard. On the **first Python call**
the wrapper re-queries `max_out()`; if it still returns 0 it falls back to the input
length `n`, then allocates. Every subsequent call takes the pre-allocated zero-copy
path. The only practical implication: make sure `_max_out` returns a valid bound by
the time the first call happens.

______________________________________________________________________

#### `--multi-output`

Each `--multi-output TYPE` adds a parallel output array, producing a tuple
return. Combine with `--variable-output`:

```sh
just-makeit method nco steps_u32_ovf --module resample \
    --arg-type void --return-type uint32_t \
    --variable-output --multi-output uint8_t
```

#### `--nogil` — release the GIL for thread-per-shard scaling

For a `--variable-output` execute method, `--nogil` wraps the pure-C kernel
call in `Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS`:

```sh
just-makeit method ddc execute --module ddc \
    --param x:"float _Complex[]" --variable-output --pass-capacity --nogil
```

```c
/* generated binding (abridged) */
const float complex *_ng0 = (const float complex *)PyArray_DATA(x_arr);
size_t _ng1 = (size_t)PyArray_SIZE(x_arr);
size_t n_out;
Py_BEGIN_ALLOW_THREADS
n_out = ddc_execute(self->handle, _ng0, _ng1, self->_execute_buf, cap);
Py_END_ALLOW_THREADS
```

The numpy accessors are hoisted into locals **before** the block so no Python
C-API runs while the GIL is dropped; the buffer realloc and any error-raising
stay above it, under the GIL. A worker that gives each thread its **own**
object and output buffer then scales across cores instead of serialising on
the GIL.

It is **opt-in** because releasing the GIL is sound only under that
one-object-per-stream contract — jm cannot verify it, so you assert it by
setting the flag. Generated, not hand-patched: the release is declarative and
regenerates with the binding.

______________________________________________________________________

## `just-makeit property <object> <prop_name> [--module name] --type TYPE [--writable] [--field]`

Add a read-only (or read-write) Python property to an existing object.

```sh
just-makeit property nco phase --module source --type uint32_t
just-makeit property nco phase_inc --module source --type uint32_t
just-makeit property buffer capacity --type size_t --writable
just-makeit property reader samples_read --module conv --type uint32_t --field
```

Like `jm method`, a computed property is **additive and splice-free**: it
injects a `get_<prop>()` declaration into `<obj>_core.h` and appends a fresh
stub to `<obj>_core.c` (plus `set_<prop>()` if `--writable`) for you to
implement, then regenerates the Python getter/setter glue in `_ext.c`. With
`--field` no stub is generated — it injects one `TYPE prop_name;` member
directly into the state struct and auto-implements the getter as
`return state->prop_name`. Existing `_core.c` bodies are never re-rendered.

**Arguments**

| Argument        | Description                                                                                                                                                                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`        | Object name (must already exist in `just-makeit.toml`).                                                                                                                                                                                                 |
| `prop_name`     | Snake-case property name.                                                                                                                                                                                                                               |
| `--module name` | Module the object belongs to (required for module objects).                                                                                                                                                                                             |
| `--type TYPE`   | C type of the property value.                                                                                                                                                                                                                           |
| `--writable`    | Also generate a setter. Without this flag the property is read-only.                                                                                                                                                                                    |
| `--field`       | Add a `TYPE prop_name;` field to the state struct and auto-implement the getter as `return state->prop_name`. No `<<IMPLEMENT>>` stub is generated — the field is the implementation. Combine with `--writable` for a read-write struct field property. |

### Removing a method or property

`just-makeit remove <object> <method|property>` regenerates the glue
(`_ext.c`, `.pyi`) without the entry, so the binding stops exposing it. It is
splice-free, so it **leaves the orphaned `_core.c` body** (and the
`_core.h` declaration, or the field-backed struct member) in place with a
"delete by hand" note — your code is never silently rewritten. Remove the
stub yourself once you're sure. (Removing *state* is structural and rebuilds
the object via the regenerate path instead.)

______________________________________________________________________

## `just-makeit function <name> --module <mod> [--param name:type ...] [--return-type TYPE] [--doc "text"]`

Add a stateless C function to an existing module — no struct, no lifecycle,
no persistent state.

Writes a C stub to the function's own sacred source file
`native/src/<module>/<name>.c` (never regenerated — your implementation is
safe) and injects the declaration into `native/inc/<module>/<module>_core.h`.
Each function thus owns one translation unit, which the module's CMakeLists
compiles into the module's OBJECT library. Then regenerates `<module>_ext.c`
to add a `_bind_<name>` Python wrapper and wire it into the `PyMethodDef`
array.

With `--inline` the function instead lives entirely as a `static inline` body
in `<module>_core.h` and gets no `.c` file.

**Arguments**

| Argument                | Description                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| `name`                  | Snake-case function name.                                                                          |
| `--module mod`          | Module the function belongs to (required).                                                         |
| `--param name:type`     | Named typed scalar parameter. Repeatable.                                                          |
| `--param name:type[]`   | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C.     |
| `--return-type TYPE`    | C return type (default: `void`).                                                                   |
| `--inline`              | Emit a `static inline` body in `<module>_core.h` instead of a separate `<name>.c`.                 |
| `--doc "text"`          | Python docstring for the function.                                                                 |
| `--impl file::funcname` | Lift the function body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub. |
| `--impl file::N:M`      | Lift lines `N`..`M` (inclusive, 1-based) instead of a named function body. Ranges error cleanly.   |
| `--replace old::new`    | String substitution applied to the body lifted by `--impl`. Repeatable.                            |

**Example — no parameters:**

```sh
just-makeit function fft_global_setup --module fft --doc "Initialize FFT tables."
```

`native/src/fft/fft_global_setup.c` (yours to implement):

```c
/*
 * fft_global_setup.c — fft module-level function.
 */
#include "fft/fft_core.h"

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

`native/src/fft/compute_window.c`:

```c
/*
 * compute_window.c — fft module-level function.
 */
#include "fft/fft_core.h"

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
