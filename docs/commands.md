# Commands

## `just-makeit new <proj> [--object name ...] [--state name:type[:default] ...]`

Create a new project. Optionally scaffold a first object in the same step.

```sh
just-makeit new my_project
just-makeit new my_project --object engine
just-makeit new my_project --object engine --state rate:double:1.0
just-makeit new my_project --object engine --state rate:double --state order:int:4
just-makeit new my_project --object gain --arg-type float --return-type float --state gain:float:1.0
just-makeit new my_filters --module filter
just-makeit new my_dsp --module osc --module env
```

`new` writes a `just-makeit.toml` that records the project name, version, and
any objects — the source of truth for all subsequent commands.

**Arguments**

| Argument                      | Description                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| `project`                     | Project name in `snake_case`. Used as the Python package name and distribution name. |
| `--object name`               | Scaffold a standalone object immediately. Repeatable.                                |
| `--module name`               | Scaffold an empty extension module immediately. Repeatable; mutually exclusive with `--object`. |
| `--state name:type[:default]` | Declare a state variable for the object. Repeatable.                                 |
| `--arg-type TYPE`             | C type for `step()` input `x`. Defaults to `float _Complex`. Use `void` for generator objects with no scalar input. Append `[]` for objects whose primary operation takes a whole buffer: `--arg-type "float _Complex[]"`. |
| `--return-type TYPE`          | C type for `step()` return value. Defaults to `--arg-type` for scalar inputs, or `void` for array inputs (`T[]`). Use `void` explicitly for sink objects that consume input but produce no scalar output. |
| `--basic`                     | Generate a plain `Makefile` instead of a CMake project. Useful for quick prototypes that don't need a full build system. |
| `--perf`                      | Generate `jm_perf.h` with `JM_HOT`, `JM_LIKELY`, and `JM_FORCEINLINE` macros and apply them to `step()`. See [Performance annotations](perf.md). |

______________________________________________________________________

## `just-makeit module <name>`

Scaffold a new Python extension module — a subpackage `.so` that groups
multiple types added via `just-makeit object`.  Must be run from the project root.

```sh
just-makeit module filter
just-makeit module osc
```

Creates:

| File | Purpose |
|------|---------|
| `native/inc/<name>/<name>_core.h` | Public C API — declare module-level functions here |
| `native/src/<name>/<name>_core.c` | Implementation — write module-level functions here |
| `native/src/<name>/<name>_ext.c` | Python binding (auto-generated) |
| `native/src/<name>/CMakeLists.txt` | Python module target |
| `src/<pkg>/<name>/__init__.py` | Subpackage init (empty exports) |

Appends `add_subdirectory(native/src/<name>)` to the root `CMakeLists.txt`
and records `[module.<name>]` with an empty `objects` list in `just-makeit.toml`.

Types are added with `just-makeit object`.  Module-level functions are added
with `just-makeit function`.

______________________________________________________________________

## `just-makeit object <name> [--module <name>] [--state name:type[:default] ...] [--arg-type TYPE] [--return-type TYPE]`

Add a stateful Python type to the project.  Use this when the algorithm needs
persistent state (history, coefficients, a cursor, a running accumulator).
For stateless operations, use [`just-makeit function`](#just-makeit-function-name---module-mod---param-nametype----return-type-type---doc-text) instead.
Must be run from the project root.

**Without `--module` — standalone object (own `.so`):**

```sh
just-makeit object engine --state rate:double:1.0
just-makeit object ema --arg-type float --return-type float --state alpha:double:0.1 --state prev:float:0.0
```

Creates the full standalone set of files, updates the top-level `CMakeLists.txt`,
and splices the import + `__all__` entry into `src/<pkg>/__init__.py`.

**With `--module` — grouped into a module subpackage `.so`:**

```sh
just-makeit object fir --module filter --state "coeffs:float[16]" --state "delay:float _Complex[16]" --state "gain:float:1.0"
just-makeit object biquad --module filter --state "b0:double:1.0" --state "a1:double:0.0" --state "w1:double:0.0"
```

**Per-object files created** (same for both modes):

| File | Purpose |
|------|---------|
| `native/inc/<obj>/<obj>_core.h` | Header: struct, inline `_step`, getters/setters |
| `native/src/<obj>/<obj>_core.c` | Source: create/destroy/reset/steps |
| `native/src/<obj>/CMakeLists.txt` | OBJECT library + C test + bench |
| `native/tests/test_<obj>_core.c` | C test with `CHECK` macro counter |
| `native/benchmarks/bench_<obj>_core.c` | C benchmark |

**Additional files for standalone objects** (no `--module`):

| File | Purpose |
|------|---------|
| `native/src/<obj>/<obj>_ext.c` | Python C extension (own `.so`) |
| `src/<pkg>/<obj>.pyi` | Type stub |
| `src/<pkg>/tests/test_<obj>.py` | pytest suite |

**Module files regenerated** after each `just-makeit object --module`:

| File | What changes |
|------|-------------|
| `native/src/<module>/<module>_ext.c` | New type block added; `PyMODINIT_FUNC` updated |
| `native/src/<module>/CMakeLists.txt` | New `<obj>_core` added to link list |
| `src/<pkg>/<module>/__init__.py` | New type added to import and `__all__` |

The module `_ext.c` is always fully regenerated from the complete object list
— never patched — so adding a third type never disturbs the first two.

**Arguments**

| Argument | Description |
|----------|-------------|
| `name` | Object name in `snake_case`. Becomes the C prefix and Python class name (title-cased). |
| `--module name` | Target module. Without this flag the object is standalone (own `.so`). |
| `--state name:type[:default]` | Declare a state variable. Repeatable. |
| `--arg-type TYPE` | C type for `step()` input. Defaults to `float _Complex`. Use `void` for generator objects with no input. Append `[]` for objects whose primary operation takes a whole buffer: `--arg-type "float _Complex[]"` — `steps()` is not generated. |
| `--return-type TYPE` | C type for `step()` return value. Defaults to `--arg-type` for scalar inputs, or `void` for array inputs (`T[]`). Use `void` explicitly for sink objects that consume input but produce no output. |
| `--perf` | Generate `jm_perf.h` and apply `JM_FORCEINLINE JM_HOT` to `step()`. |
| `--no-state` | Suppress auto-generated state variables, constructor args, and getter/setter scaffolding. Emits `<<IMPLEMENT>>` stubs in the C struct body and lifecycle functions (`create`, `destroy`, `reset`). Mutually exclusive with `--state`. Use when the constructor signature is too domain-specific to express via `--state` (e.g. a filter that takes `const float *taps, size_t num_taps`). |
| `--no-step` | Suppress `step()` and `steps()` from all C and Python output. Lifecycle functions (`create`, `destroy`, `reset`) are still generated. Use for objects whose interface consists entirely of named methods added with `jm method`. |

See [State Variable Types](types.md) for supported types, defaults, and C/Python mappings.

Each `--state name:type[:default]` generates:

- A field in the C state struct
- A constructor parameter with the declared default
- `get_name()` and `set_name()` methods in both C and Python
- Getter/setter tests in both CTest and pytest
- Reset behaviour that restores the declared default

**Naming rules**

- Lowercase letters, digits, and underscores only.
- Must not start with a digit.
- Examples: `engine`, `parser`, `rate_limiter`

The Python class name is derived automatically:

| Object name        | Python class     |
| ------------------ | ---------------- |
| `engine`           | `Engine`         |
| `rate_limiter`     | `RateLimiter`    |
| `half_band_filter` | `HalfBandFilter` |

______________________________________________________________________

## `just-makeit method <object> <method_name> [--module name] [--param name:type ...] --return-type TYPE [--variable-output] [--arg-type TYPE] [--multi-output TYPE ...]`

Add a named execute method to an existing object. Must be run from the project
root.

Each method appends a C stub to `<obj>_core.c` and regenerates the module
`_ext.c` with the new Python glue.

**Arguments**

| Argument | Description |
|----------|-------------|
| `object` | Object name (must already exist in `just-makeit.toml`). |
| `method_name` | Snake-case name for the new method. |
| `--module name` | Module the object belongs to (required for module objects). |
| `--param name:type` | Named typed scalar parameter. Repeatable. |
| `--param name:type[]` | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C. |
| `--return-type TYPE` | C type of the return value (`void` for no return). |
| `--arg-type TYPE` | C type of a single array-style input. Use `void` for count-only inputs. |
| `--variable-output` | Pre-allocate output buffer at init; return zero-copy numpy view. See below. |
| `--multi-output TYPE` | Add a second (or further) output array. Repeatable; produces a tuple return. |
| `--out-type TYPE` | Allocate a `complex64` (or other) output array per call and pass `*out` to C. The C stub receives `(... , elem_t *out)` and the Python wrapper allocates and returns the ndarray automatically. The output length equals `in_len / out_divisor`. |
| `--out-divisor N` | Divide the input length by `N` to determine the output array length when `--out-type` is active (default: 1). Use `2` for methods that interpret the input as interleaved I/Q pairs (e.g. a CI8 buffer where each complex sample is 2 bytes). |

______________________________________________________________________

### Named parameters (`--param`)

Use `--param name:type` (repeatable) when the method takes multiple distinct
typed scalar inputs. This generates named parameters in both the C stub and the
Python wrapper.

```sh
just-makeit method nco configure --module dsp \
    --param freq:float \
    --param phase:float \
    --param mode:int32_t \
    --return-type void
```

Generated C stub appended to `nco_core.c`:

```c
void
nco_configure(nco_state_t *state, float freq, float phase, int32_t mode)
{
    (void)state; (void)freq; (void)phase; (void)mode;
}
```

Generated Python wrapper in `nco_ext.c`:

```c
float freq = 0.0f;
float phase = 0.0f;
long mode_raw = 0L;
if (!PyArg_ParseTuple(args, "ffl", &freq, &phase, &mode_raw))
    return NULL;
int32_t mode = (int32_t)mode_raw;
nco_configure(self->handle, freq, phase, mode);
Py_RETURN_NONE;
```

Python call:

```python
nco.configure(0.1, 0.0, 2)
```

All scalar types in `_CTYPE_META` are supported as `--param` types (float,
double, int, int32\_t, uint32\_t, size\_t, float \_Complex, etc.).

**Array parameters** (`--param name:type[]`) generate a numpy array input.
The C stub receives `(const elem_t *name, size_t name_len)` and the Python
wrapper uses `PyArray_FROM_OTF` to convert the incoming numpy array:

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

`method` supports two output modes.  Choosing the right one depends entirely
on whether the **maximum output count is knowable at init time**.

______________________________________________________________________

#### Default — per-sample scalar or 1:1-rate array

Without `--variable-output`, the generated C stub processes **one sample**
per call.  For **array (batch) processing at a 1:1 rate** (output length =
input length, unknown at init time), the same generated stub is the correct
starting point: implement the C function to accept a pointer and length,
then write the Python glue manually following the `steps()` pattern — call
`PyArray_SimpleNew` per call, pass the data pointer to C.

```sh
just-makeit method nco steps_u32 --module source \
    --arg-type void --return-type uint32_t

just-makeit method nco steps_ctrl --module source \
    --arg-type float --return-type float
```

Generated C stub appended to `nco_core.c` — start here, then expand to a
batch signature in place:

```c
uint32_t nco_steps_u32(nco_state_t *state);
float    nco_steps_ctrl(nco_state_t *state, float x);
```

Expand to the batch signature in `nco_core.c`:

```c
void nco_steps_u32(nco_state_t *state, size_t n, uint32_t *output);
void nco_steps_ctrl(nco_state_t *state, const float *ctrl,
                    size_t n, uint32_t *output);
```

Python glue in `<module>_ext.c` (following the generated `Nco_steps` pattern):

```c
static PyObject *
Nco_steps_u32(NcoObject *self, PyObject *args)
{
    Py_ssize_t n = 1;
    if (!PyArg_ParseTuple(args, "n", &n)) return NULL;
    npy_intp dims[] = {n};
    PyObject *out = PyArray_SimpleNew(1, dims, NPY_UINT32);
    if (!out) return NULL;
    nco_steps_u32(self->handle, (size_t)n,
                  (uint32_t *)PyArray_DATA((PyArrayObject *)out));
    return out;
}
```

Python call:

```python
ph  = nco.steps_u32(1024)    # returns uint32 ndarray
out = nco.steps_ctrl(ctrl)   # ctrl is float32 ndarray; returns uint32 ndarray
```

Use the per-sample scalar form when you only need one value at a time.
Use the batch form (handwritten Python glue) for all 1:1-rate array methods.

______________________________________________________________________

#### `--variable-output` — pre-allocated zero-copy batch

With `--variable-output`, the generated code calls `<method>_max_out(state)`
at init time, pre-allocates a fixed output buffer, and returns a **zero-copy
numpy view** into that buffer on every call — **no per-call `malloc`**.

```sh
just-makeit method hbdecim execute --module resample \
    --arg-type "float _Complex" --return-type "float _Complex" \
    --variable-output

just-makeit method hbdecim execute_ovf --module resample \
    --arg-type "float _Complex" --return-type "float _Complex" \
    --variable-output --multi-output uint8_t
```

Generated C stubs appended to `hbdecim_core.c`:

```c
/* Return maximum output samples possible given current state. */
size_t hbdecim_execute_max_out(hbdecim_state_t *state);

/* Process n_in input samples; write up to _max_out results; return actual count. */
size_t hbdecim_execute(hbdecim_state_t *state,
                       const float complex *in, size_t n_in,
                       float complex *out);
```

Python call:

```python
out = decim.execute(block)   # zero-copy view; valid until next call
```

**When to use `--variable-output`**

This is the right choice when the **maximum output count is bounded by the
object's state and knowable at init time**:

| Use case | `_max_out` returns | Appropriate? |
|----------|--------------------|-------------|
| Decimator, fixed ratio `R`, block size `B` | `ceil(B / R)` | Yes |
| Buffer / FIFO with fixed capacity `C` | `C` | Yes |
| FIR filter, 1:1 rate | unknown at init | No — use per-call alloc |
| NCO extended outputs, 1:1 rate | unknown at init | No — use per-call alloc |
| Overflow/carry detector, 1:1 rate | unknown at init | No — use per-call alloc |

**Warning:** if `_max_out` returns 0 (the placeholder default), `malloc(0)`
behaviour is implementation-defined.  Always implement `_max_out` to return a
positive bound before calling the method from Python.

______________________________________________________________________

**`--multi-output`**

Each `--multi-output TYPE` adds a parallel output array.  Works with both
modes, but note: in scalar mode the extra outputs are currently not generated
— use `--variable-output` whenever you need multiple output arrays.

```sh
# uint32 phase + uint8 carry flag, batch, bounded output
just-makeit method nco steps_u32_ovf --module resample \
    --arg-type void --return-type uint32_t \
    --variable-output --multi-output uint8_t
```

______________________________________________________________________

## `just-makeit function <name> --module <mod> [--param name:type ...] [--return-type TYPE] [--doc "text"]`

Add a stateless C function to an existing module.  Use this for algorithms
that need no persistent state — no struct, no create/destroy, no lifecycle.
Must be run from the project root.

Appends a C stub to `native/src/<module>/<module>_core.c` (never regenerated
— your implementation is safe) and injects the declaration into
`native/inc/<module>/<module>_core.h`.  Then regenerates `<module>_ext.c` to
add a `_bind_<name>` Python wrapper and wire it into the `PyMethodDef` array.

**Arguments**

| Argument | Description |
|----------|-------------|
| `name` | Snake-case function name. |
| `--module mod` | Module the function belongs to (required). |
| `--param name:type` | Named typed scalar parameter. Repeatable. |
| `--param name:type[]` | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C. |
| `--return-type TYPE` | C return type (default: `void`). |
| `--doc "text"` | Python docstring for the function. |

**Without `--param`** — generates a void stub in `_core.c` and a minimal
Python wrapper in `_ext.c`:

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

`fft_ext.c` (auto-generated Python wrapper):
```c
static PyObject *
_bind_fft_global_setup(PyObject *self, PyObject *Py_UNUSED(args))
{
    (void)self;
    (void)args;
    fft_global_setup();
    Py_RETURN_NONE;
}
```

**With `--param`** — generates a typed stub in `_core.c` (with suppress lines
and a placeholder return) and a full parse block in the `_ext.c` wrapper:

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

`fft_core.h`:
```c
float compute_window(size_t n, float beta);
```

`fft_ext.c` (auto-generated):
```c
static PyObject *
_bind_compute_window(PyObject *self, PyObject *args)
{
    (void)self;
    unsigned long long n_raw = 0ULL;
    float beta = 0.0f;
    if (!PyArg_ParseTuple(args, "Kf", &n_raw, &beta))
        return NULL;
    size_t n = (size_t)n_raw;
    return PyFloat_FromDouble((double)compute_window(n, beta));
}
```

Python call:

```python
from my_pkg import fft
w = fft.compute_window(512, 5.0)
```

Implement `compute_window` in `fft_core.c` and delete the `(void)` suppression
lines.

**Array parameters** work identically to `jm method`: append `[]` to the type.
The C stub receives `(const elem_t *name, size_t name_len)`; the Python
wrapper performs `PyArray_FROM_OTF` + `Py_DECREF` automatically.

```sh
just-makeit function apply_window \
    --module fft \
    --param data:"float _Complex[]" \
    --return-type void
```

______________________________________________________________________

## `just-makeit property <object> <prop_name> [--module name] --type TYPE [--writable] [--field]`

Add a read-only (or read-write) Python property to an existing object. Must be
run from the project root.

```sh
just-makeit property nco phase --module source --type uint32_t
just-makeit property nco phase_inc --module source --type uint32_t
just-makeit property buffer capacity --type size_t --writable
just-makeit property reader samples_read --module conv --type uint32_t --field
```

Generates a `get_<prop>()` C function stub (and `set_<prop>()` if `--writable`)
that you implement in `<obj>_core.c`, plus the Python
getter (and setter) glue in the module `_ext.c`.

**Arguments**

| Argument | Description |
|----------|-------------|
| `object` | Object name (must already exist in `just-makeit.toml`). |
| `prop_name` | Snake-case property name. |
| `--module name` | Module the object belongs to (required for module objects). |
| `--type TYPE` | C type of the property value. |
| `--writable` | Also generate a setter. Without this flag the property is read-only. |
| `--field` | Add a `TYPE prop_name;` field to the state struct and auto-implement the getter as `return state->prop_name`. No `<<IMPLEMENT>>` stub is generated — the field is the implementation. Combine with `--writable` for a read-write struct field property. |

______________________________________________________________________

## `just-makeit add --state name:type[:default] [...] [--object name]`

Add one or more state variables to an existing standalone object. Must be run
from the project root.

```sh
just-makeit add --state order:int:4
just-makeit add --state threshold:double:0.5 --state window:int:64
just-makeit add --object parser --state depth:int:8
```

When the project has a single standalone object `--object` may be omitted.

`add` regenerates the six state-sensitive files from the merged state list:

- `native/inc/<obj>/<obj>_core.h`
- `native/src/<obj>/<obj>_core.c`
- `native/src/<obj>/<obj>_ext.c`
- `native/tests/test_<obj>_core.c`
- `src/<project>/<obj>.pyi`
- `src/<project>/tests/test_<obj>.py`

All six files are backed up before regeneration.  If any write fails, they
are restored and `just-makeit.toml` is left unchanged.

**Constraints**

- Each new variable name must be unique within the object's state list.
- Requires a `just-makeit.toml` — run `just-makeit new` first.

______________________________________________________________________

## `just-makeit perf`

Upgrade an existing project to use performance annotations without
overwriting any user code.  Must be run from the project root.

```sh
just-makeit perf
```

Writes `native/inc/jm_perf.h`, adds `#include "jm_perf.h"` to each object
header, and replaces `static inline` with `JM_FORCEINLINE JM_HOT` on `step()`.
Records `perf = true` in `just-makeit.toml` so future `object`/`add` commands
inherit it.  Safe to run on a project with a filled-in `step()`.  Idempotent.

See [Performance annotations](perf.md) for the full macro reference and
`JM_DEFINE_STEPS` documentation.

______________________________________________________________________

## `just-makeit config [key value]`

Show or edit the project configuration stored in `just-makeit.toml`.
Must be run from the project root.

```sh
just-makeit config                 # print current config
just-makeit config version 0.2.0  # update version
```

**Example output**

```
project:  my_project
version:  0.1.0

engine:
  rate:  double = 1.0
  order: int    = 4

parser:
  depth:  int = 8
  strict: int = 1
```

**Supported keys**

| Key       | Description                                          |
| --------- | ---------------------------------------------------- |
| `version` | Project version string stored in `just-makeit.toml`. |

______________________________________________________________________

## `just-makeit build [dir]`

Configure the CMake project (if not already done), build the C extensions, and
package a wheel via just-buildit.

```sh
just-makeit build           # wheel → dist/
just-makeit build wheels/   # wheel → wheels/
```

Must be run from a project directory containing `pyproject.toml`.

______________________________________________________________________

## `just-makeit test`

Build (if needed), then run CTest and pytest.

```sh
just-makeit test
```

- CTest runs the C tests in each object's `tests/` directory.
- pytest runs the Python tests in `src/`.

______________________________________________________________________

## `just-makeit dry-run`

Show what would be compiled and packaged without running any build steps.

```sh
just-makeit dry-run
```

Output includes the list of C source files and the full cmake configure
command that `just-makeit build` would invoke.
