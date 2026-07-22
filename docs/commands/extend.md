# Extend commands

These commands add behaviour to objects and modules that already exist in
`just-makeit.toml`. Run them from the project root after scaffolding.

______________________________________________________________________

## `just-makeit method`

```text
just-makeit method <object> <method_name>
    [--module name]
    [--param name:type ...]
    --return-type TYPE
    [--variable-output] [--arg-type TYPE]
    [--multi-output TYPE ...]
```

Add a named execute method to an existing object.

`jm method` is **additive and splice-free**: it injects the method's
declaration into `<obj>_core.h` and appends a fresh C stub to `<obj>_core.c`,
then regenerates the glue (`_ext.c`, `.pyi`) with the new Python binding.
Existing bodies in `_core.c` are never re-rendered — only the new stub is
appended, ready for you to implement.

**Arguments**

| Argument                    | Description                                                                                                                                                                                                                                                                                                                      |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`                    | Object name (must already exist in `just-makeit.toml`).                                                                                                                                                                                                                                                                          |
| `method_name`               | Snake-case name for the new method.                                                                                                                                                                                                                                                                                              |
| `--module name`             | Module the object belongs to (required for module objects).                                                                                                                                                                                                                                                                      |
| `--param name:type`         | Named typed scalar parameter. Repeatable.                                                                                                                                                                                                                                                                                        |
| `--param name:type=default` | Optional scalar parameter (e.g. `gain:double=1.0`) — omit it for the default. Makes the method keyword-capable; optional params must follow required ones; plain scalars only (gh-240).                                                                                                                                          |
| `--param name:type[]`       | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C.                                                                                                                                                                                                                                   |
| `--return-type TYPE`        | C type of the return value (`void` for no return).                                                                                                                                                                                                                                                                               |
| `--arg-type TYPE`           | C type of a single array-style input. Use `void` for count-only inputs.                                                                                                                                                                                                                                                          |
| `--variable-output`         | Pre-allocate output buffer at init; return zero-copy numpy view. See below.                                                                                                                                                                                                                                                      |
| `--multi-output TYPE`       | Add a second (or further) output array. Repeatable; produces a tuple return.                                                                                                                                                                                                                                                     |
| `--out-type TYPE`           | Allocate a `complex64` (or other) output array per call and pass `*out` to C. The C stub receives `(... , elem_t *out)` and the Python wrapper allocates and returns the ndarray automatically. The output length equals `in_len / out_divisor`.                                                                                 |
| `--out-divisor N`           | Divide the input length by `N` to determine the output array length when `--out-type` is active (default: 1). Use `2` for methods that interpret the input as interleaved I/Q pairs (e.g. a CI8 buffer where each complex sample is 2 bytes).                                                                                    |
| `--batch`                   | Generate a 1:1-rate array transform. The C stub receives `(state, const in_t *in, size_t n, out_t *out)` (or `(state, size_t n, out_t *out)` for `--arg-type void`). The Python wrapper allocates an output array of length `n` per call and returns it. Use when output length equals input length and is unknown at init time. |
| `--varargs`                 | Generate a `*args/**kwargs` Python binding. See below. Mutually exclusive with `--arg-type`, `--param`, and `--variable-output`.                                                                                                                                                                                                 |
| `--pass-capacity`           | Emit the 5-arg `(…, out, size_t max_out)` C form for a `--variable-output` method (a bounds-checking C API receives the buffer capacity).                                                                                                                                                                                        |
| `--nogil`                   | Release the GIL across the pure-C kernel of a `--variable-output` method (numpy accessors hoisted out first), so a thread-per-shard worker scales across cores. Opt-in: sound only when the object is not shared across threads concurrently (one object per stream). See below.                                                 |
| `--result-field name:T`     | Declare one field of a returned record struct (repeatable). The method returns a `list` of these record tuples; pair with `--return-type <record_struct>` (the record's C type). The result-count cap (`max_results`, default 64) is TOML-only for now — no `--max-results` CLI flag yet.                                        |
| `--single`                  | With `--result-field`, return **one** named record (a `PyStructSequence`: attribute access + unpacking) instead of a `list[tuple]`. The C kernel returns the `--return-type` record struct by value (gh-244).                                                                                                                    |
| `--record-name NAME`        | With `--single`, the public name of the record type (e.g. `ToneMetrics`), overriding the name derived from the C `--return-type` (gh-257). Also settable per method in the manifest as `record_name = "…"`.                                                                                                                      |
| `--record-module MOD`       | With `--single`, the `__module__` of the record type (e.g. `my_pkg.dsp`), so `type(r).__module__` / `repr(r)` matches the project's import path instead of the C component name. Also settable per method in the manifest as `record_module = "…"` (gh-261).                                                                     |
| `--impl file::funcname`     | Lift the method body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub.                                                                                                                                                                                                                                 |
| `--impl file::N:M`          | Lift lines `N`..`M` (inclusive, 1-based) instead of a named function body. Out-of-bounds or inverted ranges error cleanly.                                                                                                                                                                                                       |
| `--replace old::new`        | String substitution applied to the body lifted by `--impl`. Repeatable.                                                                                                                                                                                                                                                          |
| `--doc "text"`              | Python docstring for the method.                                                                                                                                                                                                                                                                                                 |
| `--py-return-type STR`      | Override the `.pyi` return-type annotation (the C `--return-type` still drives the C signature).                                                                                                                                                                                                                                 |
| `--view ClassName`          | Attach the method to a [view](#just-makeit-view) of the object instead of the object itself — adds a view-only method, or overrides a parent method's doc by reusing its name. Requires `--module`.                                                                                                                                                                                                                                                                                           |
| `--no-bench`                | Exclude this method from the generated C benchmark.                                                                                                                                                                                                                                                                              |

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
out = decim.execute(block)   # zero-copy view into the output buffer
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

**Returned views stay valid (gh-437):** accumulating the returned arrays is
safe. The binding keeps a weak reference to the view it last handed out; while
that view is still referenced, the next call allocates a fresh buffer instead
of writing over it, and the old buffer is retired and freed at dealloc. Only
the drain-immediately pattern — where the caller has dropped the previous view
by the time it calls again — reuses the buffer in place, which is the zero-copy
hot path.

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

## `just-makeit property`

```text
just-makeit property <object> <prop_name>
    [--module name]
    --type TYPE
    [--writable] [--field] [--enum NAME]
```

Add a read-only (or read-write) Python property to an existing object.

```sh
just-makeit property nco phase --module source --type uint32_t
just-makeit property nco phase_inc --module source --type uint32_t
just-makeit property buffer capacity --type size_t --writable
just-makeit property reader samples_read --module conv --type uint32_t --field
just-makeit property reader file_type --type int --field --enum ftype
```

Like `jm method`, a computed property is **additive and splice-free**: it
injects a `get_<prop>()` declaration into `<obj>_core.h` and appends a fresh
stub to `<obj>_core.c` (plus `set_<prop>()` if `--writable`) for you to
implement, then regenerates the Python getter/setter glue in `_ext.c`. With
`--field` no stub is generated — it injects one `TYPE prop_name;` member
directly into the state struct and auto-implements the getter as
`return state->prop_name`. Existing `_core.c` bodies are never re-rendered.

**Arguments**

| Argument           | Description                                                                                                                                                                                                                                             |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`           | Object name (must already exist in `just-makeit.toml`).                                                                                                                                                                                                 |
| `prop_name`        | Snake-case property name.                                                                                                                                                                                                                               |
| `--module name`    | Module the object belongs to (required for module objects).                                                                                                                                                                                             |
| `--type TYPE`      | C type of the property value.                                                                                                                                                                                                                           |
| `--writable`       | Also generate a setter. Without this flag the property is read-only.                                                                                                                                                                                    |
| `--field`          | Add a `TYPE prop_name;` field to the state struct and auto-implement the getter as `return state->prop_name`. No `<<IMPLEMENT>>` stub is generated — the field is the implementation. Combine with `--writable` for a read-write struct field property. |
| `--doc "text"`     | Python docstring for the getter (and setter, if `--writable`).                                                                                                                                                                                          |
| `--enum NAME`      | Present the property as a **string** from the named `[[enum]]` SSOT instead of the raw `int` (gh-519). See below.                                                                                                                                       |
| `--view ClassName` | Attach the property to a [view](#just-makeit-view) of the object instead of the object itself — adds a property the parent lacks, or overrides a parent property (e.g. its doc) by reusing its name. Requires `--module`.                               |

### Enum-valued properties (`--enum`)

A property whose C value is an index into a `[[enum]]` table can present that
value to Python as its **string**, exactly as a `kind = "handle"` module's
getters have always done. C still stores the `int`; only the Python face
changes.

```toml
[[enum]]
name = "ftype"
values = ["raw", "wav", "blue"]     # order IS the C int: raw=0, wav=1, blue=2

[[reader.properties]]
name = "file_type"
type = "int"
field = true
enum = "ftype"
```

```python
>>> r.file_type
'blue'
>>> r.file_type = "wav"      # only when the property is --writable
>>> r.file_type
'wav'
>>> r.file_type = "nope"
ValueError: invalid file_type 'nope' (choices: raw, wav, blue)
```

The stub types it as `Literal["raw", "wav", "blue"]`, so a typo is caught by
mypy rather than at runtime.

Two things worth knowing:

- **The value is range-checked.** If the C side holds an index the table
    cannot contain — say a format code decoded from a file header that names a
    variant you do not support — reading the property raises
    `ValueError: file_type holds out-of-range ftype value 99 (valid: 0..2)`
    rather than reading past the table (gh-521).
- **`enum` is not the same as `string_enum:`.** `enum = "name"` references the
    shared `[[enum]]` SSOT and is what properties, handle getters, and
    `--param name:enum:<ename>` use. The inline `string_enum:a,b,c` form spells
    its choices in the type itself and applies to **constructor init-params**.
    Prefer the SSOT whenever the same choice set is used in more than one place —
    that is the whole reason it exists.

`--enum` cannot be combined with a `buf_field` property: an array of enum
strings has no decoded form, and jm rejects it with a diagnostic rather than
generating something misleading.

### Removing a method or property

`just-makeit remove <kind> <name> --object <obj> [--module <mod>] [--force]`,
where `kind` is `method` or `property` (also `object`, `module`, `function`,
`state`, `warning`, or `error` for those respectively — `--object`/`--module`
only apply where relevant; `warning` and `error` are addressed differently,
see their own sections below):

```sh
just-makeit remove method configure --object nco --module dsp
just-makeit remove property phase --object nco --module dsp
```

This regenerates the glue (`_ext.c`, `.pyi`) without the entry, so the
binding stops exposing it. It is splice-free, so it **leaves the orphaned
`_core.c` body** (and the `_core.h` declaration, or the field-backed struct
member) in place with a "delete by hand" note — your code is never silently
rewritten. Remove the stub yourself once you're sure. (Removing *state* via
`just-makeit remove state <name> --object <obj>` is structural and rebuilds
the object via the regenerate path instead.)

______________________________________________________________________

## `just-makeit view`

```text
just-makeit view <object> <ViewClassName>
    --module name
    --create-fn fn
    [--init-param name:type[:default] ...]
    [--exclude-property name ...] [--exclude-method name ...]
    [--doc "text"]
```

Add a **second Python class over the same generated C core** (gh-504). The view
shares the object's `<obj>_state_t`, its `_core.c`, and its `step()`; it differs
only in the C constructor it calls, the constructor arguments it takes, and the
Python surface it exposes. Use it when one algorithm has two front doors — a
continuous mode and a burst mode, an empty accumulator and a pre-seeded one —
and a second object would mean a second copy of the C.

```sh
just-makeit view acc SeededAcc --module bank \
    --create-fn acc_create_seeded \
    --init-param seed:double:0.0 \
    --exclude-method total
```

This records a `[[<obj>.views]]` entry, injects
`acc_state_t *acc_create_seeded(double seed);` into `<obj>_core.h`, appends an
`<<IMPLEMENT>>` stub for it to the sacred `<obj>_core.c` (so the module still
compiles before you have written a line), and regenerates the module glue with
the extra class registered. The view lands in its own binding fragment,
`<mod>_ext_<viewclass>.c`, next to the parent's — **no** second core library and
no second state struct.

Views are a **module-object feature**: the multi-type module machinery is what
registers the extra class, so `--module` is required. `--create-fn` is also
required and must differ from the parent's `<obj>_create` — a view exists
precisely to build from a different constructor.

**Arguments**

| Argument                           | Description                                                                                                                                               |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`                           | Object the view sits over (must already exist in `just-makeit.toml`).                                                                                     |
| `ViewClassName`                    | Python class name for the view. Must be unique across every class the module exposes — each object's class name and every existing view.                  |
| `--module name`                    | Module the object belongs to. Required.                                                                                                                   |
| `--create-fn fn`                   | C constructor the view's `__init__` calls (e.g. `acc_create_seeded`). Required; must differ from `<obj>_create`. Scaffolded as a stub in the shared core. |
| `--init-param name:type[:default]` | The view's own constructor parameter. Repeatable; same syntax as `jm object --init-param`. Omit entirely to inherit the parent's constructor shape.       |
| `--exclude-property name`          | Parent property to omit from the view's Python surface. Repeatable; must name an existing property of the parent.                                         |
| `--exclude-method name`            | Parent method to omit. Repeatable; must name an existing `[[<obj>.methods]]` entry. The builtins `step`/`steps`/`reset` are not excludable.               |
| `--doc "text"`                     | Docstring for the view class.                                                                                                                             |

### Diverging, not only trimming

`--exclude-property` / `--exclude-method` take members *away*. To **add** a
member the parent lacks, or **override** one it has, pass `--view <ClassName>`
to [`jm property`](#just-makeit-property), [`jm method`](#just-makeit-method),
or [`jm warning`](#just-makeit-warning) — reusing a parent member's name
overrides it, a new name adds it:

```sh
# `runs` exists on SeededAcc only
just-makeit property acc runs --module bank --type size_t --field \
    --doc "reseed count" --view SeededAcc

# same name as the parent's property -> overrides its docstring on the view
just-makeit property acc depth --module bank --type size_t --field \
    --doc "seed depth" --view SeededAcc
```

These persist as `[[<obj>.views.properties]]`, `[[<obj>.views.methods]]`, and
`[[<obj>.views.warnings]]` under the matching view, and are merged over the
parent's when the glue is generated. See
[Configuration → Schema reference](../configuration.md#schema-reference).

Excluding a method drops only the view's Python wrapper and its `PyMethodDef`
entry — the shared C function stays in the core, so nothing dangles and the
parent keeps working.

**Limitation:** a view cannot yet sit over a parent whose init-params use
per-array dtype-dispatch or optional-array forms (`real_type`, `real_create_fn`,
`create_fn`, `optional`); those paths embed `<obj>_create` directly and would
silently ignore the view's `create_fn`, so `jm view` rejects them with a
diagnostic.

The [Views example](../examples/views_module.md) builds the whole thing end to
end.

______________________________________________________________________

## `just-makeit warning`

```text
just-makeit warning <object>
    --condition FIELD
    --message TEXT
    [--module name] [--category NAME] [--stacklevel N]
```

Declare a warning that fires **after construction** when a boolean state field
is set — a way to flag a degenerate-but-legal configuration without failing the
constructor. The generated `__init__` glue checks the field once the object is
built and, if set, calls `PyErr_WarnEx` with your message and category.

```sh
just-makeit warning agc underpowered \
    --message "AGC gain floor reached; output may clip" \
    --category RuntimeWarning
```

This is **declarative**: the condition, message, and category live in
`just-makeit.toml` as a `[[<object>.warnings]]` entry, and the check is
regenerated into `_ext.c` — you write no C. `--condition` names a bool-ish
field on the object's state struct (emitted as `self->handle-><field>`), so it
must already exist as state (add it with `just-makeit add` if needed).

**Arguments**

| Argument            | Description                                                                                                                                                                      |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `object`            | Object name (must already exist in `just-makeit.toml`).                                                                                                                          |
| `--condition FIELD` | Bool-valued state field that triggers the warning. Required.                                                                                                                     |
| `--message TEXT`    | Warning text shown to the Python caller. Required.                                                                                                                               |
| `--category NAME`   | Warning class — a Python built-in warning (`UserWarning`, `DeprecationWarning`, `RuntimeWarning`, …). Default `UserWarning`.                                                     |
| `--module name`     | Module the object belongs to (required for module objects).                                                                                                                      |
| `--stacklevel N`    | `PyErr_WarnEx` stacklevel, so the warning points at the caller's frame. Default `1`.                                                                                             |
| `--view ClassName`  | Attach the warning to a [view](#just-makeit-view) rather than the object, so a second front door over the shared core gets its own `PyErr_WarnEx`. Requires `--module` (gh-509). |

An object may carry more than one warning — each `just-makeit warning` call on
a distinct `--condition` adds another. Re-running with the same condition
replaces that entry.

Remove one with `just-makeit remove warning <condition> --object <obj>` — a
warning has no name of its own, so its **condition** is what identifies it.

______________________________________________________________________

## `just-makeit error`

```text
just-makeit error <object>
    --category NAME
    --message TEXT
    [--module name]
```

Translate a `create()` failure into a specific Python exception. By default a
failed constructor raises a blanket `MemoryError`; this replaces that with an
exception class and message of your choosing.

```sh
just-makeit error biquad \
    --category ValueError \
    --message "biquad coefficients unstable (|poles| >= 1)"
```

Also declarative: the choice is stored as `create_error` /
`create_error_message` in `just-makeit.toml` and regenerated into the `_ext.c`
failure path. There is **one** failure channel — a NULL from `create()` cannot
say *why* it failed — so this applies to every `create()` failure, a genuine
allocation failure included. Choose a category that reads sensibly for the most
likely cause.

**Arguments**

| Argument          | Description                                                                                                 |
| ----------------- | ----------------------------------------------------------------------------------------------------------- |
| `object`          | Object name (must already exist in `just-makeit.toml`).                                                     |
| `--category NAME` | Exception class — a Python built-in exception (`ValueError`, `RuntimeError`, `OverflowError`, …). Required. |
| `--message TEXT`  | Text for the raised exception. Required.                                                                    |
| `--module name`   | Module the object belongs to (required for module objects).                                                 |

Each object has a single failure translation, so re-running replaces it rather
than accumulating. Remove it with `just-makeit remove error <obj> --object <obj>` — `error` takes no name because there is only ever one per object.

______________________________________________________________________

## `just-makeit function`

```text
just-makeit function <name> --module <mod>
    [--param name:type ...]
    [--return-type TYPE]
    [--doc "text"]
```

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

The generated `_bind_<name>` wrapper is **positional-or-keyword**
(`METH_VARARGS | METH_KEYWORDS`): callers may pass arguments positionally or by
name (`fn(input=x, n=8)`). A no-parameter function stays `METH_NOARGS`. Keyword
capability is ~free unless keywords are actually used — see
[Arguments: positional vs keyword](../arguments.md).

**Arguments**

| Argument                        | Description                                                                                                                                                    |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                          | Snake-case function name.                                                                                                                                      |
| `--module mod`                  | Module the function belongs to (required).                                                                                                                     |
| `--param name:type`             | Named typed scalar parameter. Repeatable.                                                                                                                      |
| `--param name:type=default`     | Optional scalar parameter — omitting it yields `default` (e.g. `gain:double=1.0`). Optional params must come after required ones; plain scalars only (gh-240). |
| `--param name:type[]`           | Named numpy array parameter. Repeatable. Generates `const elem_t *name, size_t name_len` in C.                                                                 |
| `--param name:path`             | Filesystem path parameter. Python accepts `str \| os.PathLike`; C receives `const char *` via `PyUnicode_FSConverter` (gh-353).                                |
| `--param name:enum:<ename>[=d]` | String-choice parameter validated against the named `[[enum]]` SSOT; C receives the `int` index; optional default `d` is the string value (gh-353).            |
| `--return-type TYPE`            | C return type (default: `void`).                                                                                                                               |
| `--check-return`                | Treat a non-zero `int` return as failure: raises `RuntimeError(rc)`, returns `None` on success. Requires an integer `--return-type` (gh-363).                  |
| `--inline`                      | Emit a `static inline` body in `<module>_core.h` instead of a separate `<name>.c`.                                                                             |
| `--doc "text"`                  | Python docstring for the function.                                                                                                                             |
| `--impl file::funcname`         | Lift the function body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub.                                                             |
| `--impl file::N:M`              | Lift lines `N`..`M` (inclusive, 1-based) instead of a named function body. Ranges error cleanly.                                                               |
| `--replace old::new`            | String substitution applied to the body lifted by `--impl`. Repeatable.                                                                                        |

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

**Path parameters** (`name:path`) accept a `str | os.PathLike` from Python,
coerce it to bytes via `PyUnicode_FSConverter`, and forward `const char *` to C
— the same coercion used by the handle generator:

```sh
just-makeit function load_calibration \
    --module dsp \
    --param path:path \
    --return-type void
```

Python call:

```python
from pathlib import Path
import my_pkg.dsp as dsp
dsp.load_calibration(Path("/data/cal.bin"))
dsp.load_calibration("/data/cal.bin")        # str also works
```

**Enum parameters** (`name:enum:<ename>[=default]`) accept a choice string,
validate it against the `[[enum]]` SSOT in `just-makeit.toml`, and forward
the `int` index to C. Requires a `[[enum]]` with the matching `name` to be
declared in `just-makeit.toml` first.

```toml
# just-makeit.toml
[[enum]]
name = "color_space"
values = ["rgb", "hsv", "lab"]
```

```sh
just-makeit function convert_image \
    --module img \
    --param path:path \
    --param src_cs:enum:color_space=rgb \
    --param dst_cs:enum:color_space \
    --return-type int \
    --check-return
```

**`--check-return`** makes the generated binding treat a non-zero `int`
return value as a failure: it captures the result, raises `RuntimeError` on
a non-zero code, and returns `None` on success. Requires `--return-type` to
be an integer type (`int`, `size_t`, …). It is the module-function analog of
the handle generator's `close_returns` and composes naturally with path and
enum args.

```python
from my_pkg import img
img.convert_image("input.png", dst_cs="lab")   # → None, or raises RuntimeError
```

C stub (`native/src/img/convert_image.c` — yours to implement):

```c
#include "img/img_core.h"

int
convert_image(const char *path, int src_cs, int dst_cs)
{
    /* <<IMPLEMENT: convert_image>> */
    return 0;
}
```
