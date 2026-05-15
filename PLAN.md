# just-makeit remaining work plan

## Status summary (2026-05-10)

Tests: 439 passed, 32 skipped.  All features through `--array-arg` are done.

______________________________________________________________________

## Step 3: `just-makeit function`

### What it does

Adds a module-level Python function (no type object, no handle) to an existing
module.  The canonical use case: FFT module-level API, window utility
functions, global setup calls.

```
just-makeit function fft_global_setup --module fft
just-makeit function window_kaiser    --module fft
```

### What gets generated

**`native/src/{module}/{module}_functions.c`** (created once, never regenerated):

```c
#include <Python.h>
#include <numpy/arrayobject.h>
/* add your own includes */

/* <<IMPLEMENT: fft_global_setup>> */
static PyObject *
fft_global_setup(PyObject *self, PyObject *args)
{
    Py_RETURN_NONE;
}
```

**`native/src/{module}/{module}_ext.c`** footer — currently hard-codes
`.m_methods = NULL`.  When any function exists, regenerated to:

```c
static PyMethodDef Filter_methods[] = {
    {"fft_global_setup", fft_global_setup, METH_VARARGS, "fft_global_setup."},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef filter_moduledef = {
    ...
    .m_methods = Filter_methods,
};
```

The function stubs section must be `#include`d (or inlined) before the
`PyMethodDef` array. Easiest approach: include the functions file:

```c
/* in {module}_ext.c header section */
#include "{module}_functions.c"
```

Or emit it as a separate OBJECT lib in CMakeLists and link it in.

**Simpler approach**: just append the function body directly into
`{module}_functions.c` and `#include "{module}_functions.c"` at the top of
`{module}_ext.c` (after the existing `#include` block in
`MODULE_EXT_C_HEADER`).  Functions file is never regenerated; ext.c is
regenerated when functions are added, updating the `PyMethodDef` array and
`.m_methods`.

### Template changes required

**`MODULE_EXT_C_HEADER`** — add:

```
<<module_functions_include>>
```

Empty string when no functions; `#include "{module}_functions.c"\n` when any
exist.

**`MODULE_EXT_C_FOOTER`** — replace `.m_methods = NULL` with:

```
<<module_methods_def>>
...
    .m_methods = <<module_m_methods>>,
```

Where `module_methods_def` is either empty string (no functions) or the full
`PyMethodDef` array, and `module_m_methods` is either `NULL` or `Filter_methods`.

**New function** `make_functions_ctx(module, Module, functions)` returns
`module_methods_def`, `module_m_methods`, `module_functions_include`.

### Config storage

Under the module entry — parallel to `objects`:

```toml
[module.fft]
objects = []

[[module.fft.functions]]
name = "fft_global_setup"
doc  = "Global FFT setup."

[[module.fft.functions]]
name = "window_kaiser"
doc  = "Kaiser window function."
```

New config accessors: `module_functions(cfg, module)`, `add_module_function(cfg, module, fn)`.
Update `_dump()`.

### Files to create/touch

| File                            | Change                                                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `src/just_makeit/_function.py`  | New — `run(root, fn_name, module, doc)`                                                                               |
| `src/just_makeit/_cli.py`       | Add `function` subcommand                                                                                             |
| `src/just_makeit/_templates.py` | `make_functions_ctx()`, update `MODULE_EXT_C_HEADER` + `MODULE_EXT_C_FOOTER`, add defaults to `render_module_ext_c()` |
| `src/just_makeit/_config.py`    | `module_functions()`, `add_module_function()`, update `_dump()`                                                       |
| `src/just_makeit/_object.py`    | `_regenerate_module()` must read and pass functions ctx                                                               |

### Validation / edge cases

- `--module` required (functions must belong to a module, not standalone)
- Duplicate function name → exit 1
- `{module}_functions.c` is created on first function; subsequent functions
  append to it (same pattern as `_methods.c`)

### Tests to write

`tests/test_function.py`:

- `{module}_functions.c` created with stub
- `{module}_ext.c` footer has `PyMethodDef` array and `.m_methods = {Module}_methods`
- `{module}_ext.c` header has `#include "{module}_functions.c"`
- Second function appends to functions file, adds entry to PyMethodDef
- Config records `[[module.fft.functions]]`
- No stray placeholders
- Missing module → exit 1; duplicate name → exit 1

______________________________________________________________________

## Step 4: Doppler migration

### Filesystem layout

```
/home/hunterdsp/doppler/
  c/include/dp/      C headers (nco.h, fft.h, hbdecim.h, resamp.h, ...)
  python/ext/        Current hand-written extensions
  python/src/        Python wrappers
  CMakeLists.txt     Top-level build
  pyproject.toml
```

Current extensions (`python/ext/`):

| File              | Lines | Assessment                                                              |
| ----------------- | ----- | ----------------------------------------------------------------------- |
| `_nco.c`          | 477   | Scaffold + hand-write methods.c                                         |
| `_hbdecim.c`      | 250   | Scaffold with `--array-arg h:float32`, hand-write execute               |
| `_resamp.c`       | 278   | Hand-write init (2D shape dims needed); scaffold rest                   |
| `_ddc.c`          | 306   | Clean scaffold, scalars only                                            |
| `_buffer.c`       | 673   | Fully hand-written (double-mapped mmap, not a standard object)          |
| `_fft.c`          | 258   | Module-level only; `just-makeit function` for wiring, hand-write bodies |
| `_accumulator.c`  | 498   | Hand-written additions                                                  |
| `_delay.c`        | 299   | Scaffold + hand-write execute                                           |
| `_stream.c`       | 940   | Fully hand-written (complex state machine)                              |
| `_resamp_dpmfs.c` | 287   | Variant of Resamp; same constraints                                     |
| `dp_window.c`     | 159   | Module-level functions (like FFT)                                       |

### Component-by-component breakdown

______________________________________________________________________

#### NCO

**Scaffold command:**

```
just-makeit object nco --module dsp --state "norm_freq:float:0.0f"
```

**Execute methods** (6 variants) — all hand-write in `_methods.c`:

- `execute_cf32(n=1)` → `complex64[n]` — free-running, no input
- `execute_cf32_ctrl(x_or_n)` → `complex64[n]` — takes float32 control array OR int n
- `execute_u32(n=1)` → `uint32[n]`
- `execute_u32_ctrl(x_or_n)` → `uint32[n]`
- `execute_u32_ovf(n=1)` → `(uint32[n], uint8[n])` tuple
- `execute_u32_ovf_ctrl(x_or_n)` → `(uint32[n], uint8[n])` tuple

The `_ctrl` variants dispatch on arg type (int vs array) — cannot be expressed
with `just-makeit method`.  Hand-write all 6 in `_methods.c`.

**Why not `just-makeit method`?** The ctrl variants have dual-dispatch logic
(check `PyLong_Check`); `--variable-output` assumes a pre-allocated buffer
sized by `_max_out()`; neither maps here.

**State getters/setters** — `norm_freq` generates `get_norm_freq`/`set_norm_freq`
automatically. Doppler uses `get_freq`/`set_freq` — either rename state var to
`freq` or rename in `_methods.c`.

**Properties** — `get_phase` and `get_phase_inc` return read-only computed values:

```
just-makeit property nco phase     --type uint32_t
just-makeit property nco phase_inc --type uint32_t
```

______________________________________________________________________

#### HBDecim

**Scaffold command:**

```
just-makeit object hbdecim --module dsp \
    --array-arg "h:float32" \
    --state "num_taps:size_t:1"
```

Note: in doppler, `num_taps` is passed separately from `h` to `dp_hbdecim_cf32_create(num_taps, h)`.
With `--array-arg`, just-makeit generates `create(h_ptr, h_len, num_taps)`.
Since `h_len == num_taps`, drop the redundant scalar: just use `h_len`.
Implement `hbdecim_create(const float *h, size_t h_len)` in `_core.c` stub.

**Properties:**

```
just-makeit property hbdecim rate     --type double
just-makeit property hbdecim num_taps --type size_t
```

**Execute:**
The doppler execute uses per-call alloc + slice, not pre-allocated buffer:

```c
size_t max_out = (num_in + 1) / 2 + num_taps + 2;
PyObject *out = PyArray_SimpleNew(1, &max_out, NPY_COMPLEX64);
size_t n_out = dp_hbdecim_cf32_execute(...);
// return trimmed slice
```

**Options:**
a. Use `--variable-output`: implement `hbdecim_execute_max_out()` with the
worst-case formula; returns a pre-allocated view. Caller always gets
max_out samples (zero-padded). If the caller needs the exact count, they
must slice in Python. This is the zero-alloc path.
b. Hand-write execute in `_methods.c` using per-call alloc + trim (matches
existing doppler behavior exactly). Recommended for correctness match.

Recommendation: **option b** — hand-write execute in `_methods.c`. Don't use
`just-makeit method` for execute on these.

______________________________________________________________________

#### Resamp

**Init pattern** — `dp_resamp_cf32_create(L, N, bank_ptr, rate)` needs
the 2D shape dimensions (L = phases, N = taps/phase) separately.

**Limitation**: `--array-arg "bank:float32"` generates `create(bank_ptr, bank_len, rate)`
where `bank_len = L*N` total — C can't recover L and N from this.

**Options:**
a. Add `--array-arg-2d` support: generate `create(bank_ptr, L, N, rate)` from
`PyArray_DIM(arr, 0)` and `PyArray_DIM(arr, 1)`. Needs a new flag.
b. Hand-write `__init__` — override in `_methods.c`. Since `_init` is currently
only in `_ext.c` (generated), this means the `_create()` stub in `_core.c`
is never called from Python and can be left as a no-op. Instead, override
`_init` by writing a custom `ResampCf32_init` in `_methods.c` and
patching `tp_init` in the generated type. This is awkward.
c. **Cleanest**: scaffold without `--array-arg`, mark `_init` as custom in
`_methods.c`, and call `dp_resamp_cf32_create()` directly there.
The generated `_core.c` stub `resamp_create()` is ignored.

Recommendation: **option c** for Resamp — scaffold with scalars only, hand-write
`__init__` in `_methods.c`. Note: this requires a way to override `tp_init`
from `_methods.c`, which the generated ext.c doesn't support.

**Alternative (practical)**: scaffold completely by hand for Resamp — it's
278 lines total, all custom. Don't scaffold it at all with just-makeit.

______________________________________________________________________

#### DDC

**Scaffold command (clean fit):**

```
just-makeit object ddc --module dsp \
    --state "norm_freq:float:0.0f" \
    --state "num_in:int:1024" \
    --state "rate:double:1.0"
```

**Execute:**
Same as HBDecim — per-call alloc + formula for max_out. Hand-write in `_methods.c`.

**Additional methods:** `set_freq`, `get_freq` — generated from state var `norm_freq`.

______________________________________________________________________

#### Buffer

**Recommendation: fully hand-written.** The `dp_f32` type is a double-mapped
circular buffer; `wait()` returns a zero-copy view into mmap'd memory;
`consume()` advances the read pointer. There are 3 buffer types
(F32Buffer, F64Buffer, I16Buffer). None of this maps to just-makeit patterns.

Write a single `_buffer.c` equivalent in `_methods.c` (or a dedicated file)
and wire it into the module manually.

______________________________________________________________________

#### FFT

**Scaffold command:**

```
just-makeit module fft              # create the module
just-makeit function fft_global_setup --module fft
just-makeit function fft1d_execute   --module fft
just-makeit function fft1d_execute_inplace --module fft
just-makeit function fft2d_execute   --module fft
just-makeit function fft2d_execute_inplace --module fft
```

This wires up `PyMethodDef` and `.m_methods`.  The function bodies in
`fft_functions.c` are fully hand-written (dtype dispatch, shape handling).

______________________________________________________________________

#### Accumulator / Delay

**Delay (299 lines):** Simple scalar state + `execute(x) -> cf32 array`.

```
just-makeit object delay --module dsp --state "delay_samples:size_t:0"
```

Hand-write `execute` in `_methods.c` (variable-output with known size = delay_samples).

**Accumulator (498 lines):** More complex state.  Scaffold scalars + hand-write
all execute variants in `_methods.c`.

______________________________________________________________________

#### Stream (940 lines)

Fully hand-written.  Complex state machine, not expressible in just-makeit.

______________________________________________________________________

### Migration sequence

Once `just-makeit function` exists (Step 3), execute in this order:

```bash
cd /home/hunterdsp/doppler
git init && git add -A && git commit -m "snapshot before just-makeit"
git checkout -b just-makeit-port

# 1. Scaffold project (just the framework, no components yet)
just-makeit new doppler /home/hunterdsp/doppler \
    --module dsp --module fft
# (may need manual pyproject.toml reconciliation)

# 2. NCO
just-makeit object nco --module dsp --state "norm_freq:float:0.0f"
just-makeit property nco phase     --module dsp --type uint32_t
just-makeit property nco phase_inc --module dsp --type uint32_t
# → hand-write 6 execute variants in native/src/nco/nco_methods.c

# 3. HBDecim
just-makeit object hbdecim --module dsp --array-arg "h:float32"
just-makeit property hbdecim rate     --module dsp --type double
just-makeit property hbdecim num_taps --module dsp --type size_t
# → hand-write execute in native/src/hbdecim/hbdecim_methods.c

# 4. DDC
just-makeit object ddc --module dsp \
    --state "norm_freq:float:0.0f" \
    --state "num_in:int:1024" \
    --state "rate:double:1.0"
# → hand-write execute in native/src/ddc/ddc_methods.c

# 5. Delay
just-makeit object delay --module dsp --state "delay_samples:size_t:0"
# → hand-write execute in native/src/delay/delay_methods.c

# 6. FFT module-level functions
just-makeit function fft_global_setup        --module fft
just-makeit function fft1d_execute           --module fft
just-makeit function fft1d_execute_inplace   --module fft
just-makeit function fft2d_execute           --module fft
just-makeit function fft2d_execute_inplace   --module fft
# → hand-write bodies in native/src/fft/fft_functions.c

# 7. Hand-written objects (copy from python/ext/*.c):
#    Resamp, Buffer, Accumulator, Stream, ResampDpmfs, dp_window
#    These go directly into their module's ext.c or _methods.c
```

______________________________________________________________________

### Known gaps / future just-makeit features

| Gap                                                | Severity | Fix                                                 |
| -------------------------------------------------- | -------- | --------------------------------------------------- |
| Resamp: 2D array needs L and N dims separately     | Medium   | Add `--array-arg-2d name:dtype` variant             |
| `_ctrl` execute: dual dispatch (int vs array)      | Medium   | Hand-write; `just-makeit method` can't express this |
| execute with per-call alloc + trim (not pre-alloc) | Low      | Hand-write; use `_methods.c` override               |
| `tp_init` override from `_methods.c`               | Low      | Out of scope; scaffold without `_init` override     |
| Classmethods (FIR `from_real()`)                   | Low      | Hand-add to generated ext.c after scaffolding       |

______________________________________________________________________

### What "done" looks like

After the migration:

- `just-makeit build` compiles cleanly
- `just-makeit test` passes all existing doppler Python tests
- The generated `just-makeit.toml` captures the full component/method/property/function topology
- `just-makeit object` + `just-makeit method` commands can add new components without touching the existing hand-written code
