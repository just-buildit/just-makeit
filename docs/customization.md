# Customizing your project

The generated project is a starting point. Most extensions are one command
away — reach for the editor only when implementing your actual DSP logic.

______________________________________________________________________

## What regenerates vs what's yours

just-makeit follows a **sacred/glue contract**: glue files are rebuilt from
the manifest on every mutating command (`method`, `property`, `function`,
`apply`), while sacred files — your algorithm — are never spliced or
re-rendered once they exist.

| File                                 | Class      | Notes                                                                        |
| ------------------------------------ | ---------- | ---------------------------------------------------------------------------- |
| `native/src/<obj>/<obj>_core.c`      | **sacred** | implement `step()` / `steps()` / lifecycle here; never spliced, only rebuilt |
| `native/inc/<obj>/<obj>_core.h`      | **mixed**  | the state struct + inline `step()` are sacred; method/property decls refresh |
| `native/src/<obj>/<obj>_ext.c`       | **glue**   | Python binding — regenerated, don't edit                                     |
| `native/src/<module>/<module>_ext.c` | **glue**   | module binding — fully rewritten on each `object --module`                   |
| `native/src/<obj>/CMakeLists.txt`    | **glue**   | OBJECT library + test + bench targets                                        |
| `native/tests/test_<obj>_core.c`     | **yours**  | add assertions here; not overwritten                                         |
| `src/<pkg>/<obj>.pyi`                | **glue**   | type stub — matches generated binding                                        |
| `src/<pkg>/tests/test_<obj>.py`      | **yours**  | add pytest cases here; not overwritten                                       |

**Rule of thumb:** `_ext.c`, `.pyi`, and `CMakeLists.txt` are glue (owned by
the generator). `_core.c` and the test files are yours. In `_core.h` the
state struct and inline `step()` are sacred; only the method/property
*declarations* follow the manifest.

The additive verbs (`jm method`, computed `jm property`, `jm function`) are
splice-free — they inject one declaration into `_core.h` and append a fresh
stub to `_core.c`; they never re-render an existing body. Adding state with
`jm add` is **structural**: it rebuilds the object from the manifest (see
below). The two commands that rebuild the sacred `_core.c` are `jm add` and
`jm regenerate <obj>` — `git stash` first, or keep your body in the TOML
`impl`/`create_impl` so the rebuild re-asserts it (see
[Declarative scaffolding](declarative-scaffolding.md#jm-regenerate-component-the-deliberate-refresh)).

______________________________________________________________________

## Typical workflow after scaffolding

1. Scaffold with state variables: `just-makeit new my_filter --object fir --state "coeffs:float[16]" --state "delay:float[16]"`
1. Open `native/src/fir/fir_core.c` — implement `fir_step()`.
1. Build and test: `make && make test`.
1. Add more state: `just-makeit add --object fir --state gain:float:1.0f` → **rebuilds** the object from the manifest, so keep your algorithm in the TOML `impl`/`create_impl` (or `git stash` first); the new field lands in the struct, constructor, getter/setter, and reset.
1. If you need a struct field that isn't a state variable (e.g. a scratch buffer), add it manually to the struct in `native/inc/fir/fir_core.h` — the struct is sacred, so `jm apply` never re-renders it and your extra fields survive (a `jm add`/`jm regenerate` rebuild does re-stub the struct from the manifest, so re-add them after).

______________________________________________________________________

## 1. Declare your state variables upfront

Use `--state name:type[:default]` when running `new` or `object` so the
scaffolding matches your object from the start:

```sh
just-makeit new my_filter \
    --state cutoff_freq:float:440.0f \
    --state num_taps:int32_t:32
```

This generates the struct, constructor parameters, getter/setter pairs,
reset behaviour, and Python type stubs for each variable in one shot.

______________________________________________________________________

## 2. Add state variables to an existing object

```sh
just-makeit add --object my_filter --state drive:float:1.0f
```

Adding state is **structural**: `add` writes the new `[[my_filter.state]]`
entry to `just-makeit.toml`, then rebuilds the object from the manifest (a
delete-then-apply). The new field reaches the struct, constructor,
getter/setter, reset, and Python stub in one shot. Because the rebuild
re-stubs the sacred `_core.c`, keep your algorithm in the TOML
`impl`/`create_impl` (the rebuild re-asserts it) or `git stash` first. `add`
prompts once before rebuilding; `--force` skips it.

Use this for any scalar state variable that follows the standard lifecycle
(constructor parameter, getter/setter, reset target). For non-scalar fields
(arrays, pointers, structs) add them manually as described below.

______________________________________________________________________

## 3. Add a second standalone object

```sh
just-makeit object bpf \
    --state center_freq:float:1000.0f \
    --state bandwidth:float:200.0f    \
    --state order:int32_t:4
```

Adds a `bpf/` object directory, updates `CMakeLists.txt`, registers the
object in `just-makeit.toml`, and adds the Python type stub and test.
See the [Workflow](workflow.md) page for the full multi-object layout.

______________________________________________________________________

## 4. Implement `step`

Open `<component>/src/<component>_core.c` and replace the pass-through stub:

```c
static inline float complex
my_filter_step(const my_filter_state_t *state, float complex x)
{
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}
```

Reads `state->cutoff_freq`, `state->num_taps`, etc. to process `x`. The
function is `static inline` in the header for maximum performance in the hot
path.

______________________________________________________________________

## 5. Add non-scalar state manually

For fields that don't fit the scalar pattern (fixed-size arrays, heap
allocations, nested structs), add them directly to the struct in
`<component>/inc/<component>/<component>_core.h` — the struct is sacred, so
`jm apply` never re-renders it and your manual fields survive (a `jm add` /
`jm regenerate` rebuild re-stubs the struct from the manifest, so re-add them
after one):

```c
typedef struct {
    float    cutoff_freq;
    int32_t  num_taps;
    float  coeffs[64];       /* add manually */
    float  delay_line[64];   /* add manually */
} my_filter_state_t;
```

Then implement any corresponding logic in `<component>_core.c` and expose
new getters/setters in `<component>_ext.c` if needed.

______________________________________________________________________

## 6. Expose new Python methods

Add new C functions to the header, implement them in the `.c` file, then
expose them in `<component>_ext.c`. Each Python method follows this skeleton:

```c
static PyObject *
MyFilter_my_method(MyFilterObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    /* parse args, call C function, return result */
}
```

Add an entry to `MyFilter_methods[]`:

```c
{"my_method", (PyCFunction)MyFilter_my_method, METH_VARARGS,
 "Brief description."},
```

Update the type stub `src/<package>/<component>.pyi` to match.

______________________________________________________________________

## 7. Add CTest tests

`<component>/tests/test_<component>_core.c` already has a template test.
Add more assertions inline, or register additional executables in the
component's `CMakeLists.txt`:

```cmake
add_executable(test_my_filter_edge tests/test_edge_cases.c)
target_link_libraries(test_my_filter_edge PRIVATE my_filter_core)
target_include_directories(test_my_filter_edge PRIVATE
    inc ${CMAKE_SOURCE_DIR}/inc)
add_test(NAME test_my_filter_edge COMMAND test_my_filter_edge)
```

______________________________________________________________________

## 8. Add dependencies

Link a third-party library (FFTW, libsndfile, etc.) in the component's
`CMakeLists.txt`:

```cmake
find_package(FFTW3f REQUIRED)
target_link_libraries(my_filter_core PRIVATE FFTW3::fftw3f)
```

For Python runtime dependencies, add them to `pyproject.toml`:

```toml
[project]
dependencies = [
    "numpy",
    "scipy",
]
```
