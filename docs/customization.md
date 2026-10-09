# Customizing your project

The generated project is a starting point. Most extensions are one command
away — reach for the editor only when implementing your actual DSP logic.

______________________________________________________________________

## What regenerates vs what's yours

just-makeit follows a **sacred/glue contract**: glue files are rebuilt from
the manifest on every mutating command (`method`, `property`, `function`,
`apply`), while sacred files — your algorithm — are never re-rendered once
they exist; `apply` only appends a definition the manifest declares and the
file lacks. Every file and kind is in the canonical table,
[Who owns each file](workflows/edit-lifecycle.md#who-owns-each-file); the ones
you edit most:

| File                                                                   | Class                                    | Notes                                                                                                                                  |
| ---------------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `native/src/<obj>/<obj>_core.c`                                        | **yours**                                | `steps()`, lifecycle and method bodies; `apply` only appends a definition the manifest declares and the file lacks                     |
| `native/inc/<pkg>/<obj>/<obj>_core.h`                                  | **yours**                                | the state struct + inline `step()` are sacred; method/property decls follow the manifest                                               |
| `native/src/<obj>/<obj>_ext.c`                                         | **jm's**                                 | Python binding — regenerated, don't edit                                                                                               |
| `native/src/<module>/<module>_ext.c`                                   | **jm's**                                 | module aggregator — rewritten on every `apply` and `object --module`                                                                   |
| `native/src/<module>/<module>_ext_<obj>.c`                             | **shared**                               | a module object's binding: `apply` adds missing members, never re-renders one you changed; `jm adopt <obj>` makes it jm's              |
| `native/src/<obj>/CMakeLists.txt`                                      | **jm's**                                 | OBJECT library + test + bench targets                                                                                                  |
| `native/tests/test_<obj>_core.c`                                       | **yours**                                | add assertions here; not overwritten                                                                                                   |
| `native/tests/test_<obj>_symbols.c`                                    | **derived**                              | the address of every function the binding calls, linked into the C test so one declared and never defined fails at link time (gh-1361) |
| `src/<pkg>/<obj>.pyi`                                                  | **jm's**                                 | type stub — matches generated binding                                                                                                  |
| `src/<pkg>/tests/test_<obj>.py`, `src/<pkg>/benchmarks/bench_<obj>.py` | **jm's until you delete its first line** | rewritten on `apply` while it starts `# jm:generated`; delete that line before adding cases                                            |

**Rule of thumb:** `_ext.c`, `.pyi`, and a component's `CMakeLists.txt` are
glue (owned by the generator). `_core.c` and the C tests are yours. In
`_core.h` the state struct and inline `step()` are sacred; only the
method/property *declarations* follow the manifest.

The additive verbs (`jm method`, computed `jm property`, `jm function`) are
splice-free — they inject one declaration into `_core.h` and append a fresh
stub to `_core.c` (for `jm function`, the module's `<mod>_core.h` and a new
`native/src/<mod>/<fn>.c` unless `--functions-in-core`); they never
re-render an existing body. Adding state with `jm add` is **structural**: it
rebuilds the object from the manifest (see below). The two commands that rebuild the sacred `_core.c` are `jm add` and
`jm regenerate <obj>`, but they don't treat your hand-written bodies the same
way: `jm regenerate` lifts create/destroy/reset/`step()`/getter/setter/method
bodies out by function name before deleting the files and splices them back
into the freshly generated ones (`--discard` skips this for a clean reset),
while `jm add` always does a clean rebuild — the old body's signature predates
the new field, so there's nothing safe to splice. Either way, `git stash`
first, or keep your body in the TOML `impl`/`create_impl` so the rebuild
re-asserts it (see
[Declarative scaffolding](declarative-scaffolding.md#jm-regenerate-component-the-deliberate-refresh)).

______________________________________________________________________

## Hand-written C in a module's binding

A module's `<module>_ext.c` aggregator is glue and is rewritten every time,
but it wires in three files that jm **never creates and never modifies**. Each
is discovered by existing on disk — there is no manifest key to set — and each
is included at a different point, which is the only thing that distinguishes
them.

| File                         | Included                     | Use it for                                      |
| ---------------------------- | ---------------------------- | ----------------------------------------------- |
| `<module>_ext_prologue.c`    | **before** every fragment    | C that two objects' bindings share              |
| `<module>_ext_<obj>_extra.c` | after that object's fragment | a hand-owned binding for one object             |
| `<module>_ext_extra.c`       | after all fragments          | a hand-written CPython type (see `extra_types`) |

A method you write in `<module>_ext_<obj>_extra.c` (or a standalone object's
`<comp>_ext_extra.c`) is reachable only through a row in the object's method
table, and that table is jm's once the fragment is. Declare the row as an
[`[[<obj>.extra_methods]]`](configuration.md#componentextra_methods-entries)
entry and jm writes it, a prototype for it and the `.pyi` member (gh-1997).

### Your own CMake: `<dir>_extra.cmake`

Every `native/src/<dir>/CMakeLists.txt` jm writes is glue too, and ends by
including one file beside it that jm never creates, modifies or deletes:

```cmake
include(${CMAKE_CURRENT_LIST_DIR}/<dir>_extra.cmake OPTIONAL)
```

Put anything the manifest cannot express there — a
`target_compile_definitions`, a compile option, a `find_package`, a
hand-written test or benchmark target (`jm status` reads the hook when it asks
whether one is built, gh-1432) — rather than
in the generated file, where the next `jm apply` would drop it (gh-1351).
`OPTIONAL` makes the line do nothing until you create the file. It is named for
the directory, so a module object that shares its module's directory shares its
hook. `jm regenerate` and `jm remove` keep it, and an apply that is about to
drop a statement jm itself never writes names it and points here.

An OBJECT library of your own belongs here too, with the statements that
configure it. `jm apply` refuses to rewrite a generated CMakeLists that
declares one, writing nothing and naming the hook to move it to: the rewrite
would erase it, and leave whatever wires it naming a target that is gone
(gh-1840). Fold it into the C library from the hook or from the root
`CMakeLists.txt` (not directly beneath an `add_subdirectory()` line, which
`apply` rewrites):

```cmake
add_library(engine_helpers OBJECT helpers.c)
set_target_properties(engine_helpers PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_sources(<pkg>_lib PRIVATE $<TARGET_OBJECTS:engine_helpers>)
target_sources(<pkg>_lib_static PRIVATE $<TARGET_OBJECTS:engine_helpers>)
```

`jm status` reads a library declared in the hook like one in a CMakeLists:
`UNWIRED` names it while nothing folds it in, and a root line wiring it is
not mistaken for one naming a target that is gone.

The prologue exists because the other two cannot serve the shared case: a
helper included *after* its callers is not available to them, so two objects
needing the same hand-written function had nowhere to put it that both
fragments could call. Duplicating it into both sacred fragments is the trap it
replaces — a fix applied to one copy leaves the other silently wrong.

Names use the module's flat form, so a nested id `dsp.filters` looks for
`dsp_filters_ext_prologue.c` in `native/src/dsp_filters/`.

______________________________________________________________________

## Hand-owning one member of a generated stub

The table above calls `.pyi` **jm's**, and by default it is — regenerated in
full on every mutating command. But there is an escape hatch for a single
member, which matters when the runtime has something the manifest cannot
describe.

The case that motivates it: you restore a `close()` in the sacred `_ext.c`
fragment because that is the name your type has always used and every caller
expects, while jm's object shape names the destructor `destroy()`. The runtime
has `close()`; the regenerated stub does not; a type checker rejects
`r.close()`. Hand-writing the binding is a complete answer for *behaviour* and,
without this, only a partial one for *typing*.

Put a `# jm:hand` comment directly above the member in the `.pyi`:

```python
    # jm:hand
    def close(self) -> None:
        """Close the reader (hand-written in the sacred fragment)."""
```

That member is now yours. Every subsequent `jm apply` transplants it verbatim
into the fresh render, marker included, so it keeps surviving. It needs **no
manifest entry at all** — jm never emits a placeholder for it, and one is not
required.

There are two ways to mark a member hand-owned, for two different situations:

| Mechanism            | Use when                                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `# jm:hand` comment  | The member has **no manifest counterpart** — a hand-added CPython overload, or a method whose name jm would not generate.            |
| `manual_stub = true` | A declared method whose C binding you own entirely (spliced into a sacred `_ext_<obj>_extra.c`), so jm's stub is only a placeholder. |

Both funnel through the same splice, and the behaviour depends on whether the
freshly rendered stub has a member of that name:

- **It does** (a manifest-derived member you hand-edited in place) — the
    rendered span is replaced with your text, verbatim.
- **It does not** (a purely hand-written addition) — your member is appended
    after the last member of its class.

Two things to know before relying on it:

- **A property's getter and setter are one unit.** They share a Python name, so
    marking either marks both. Splicing only the getter and leaving a stale
    setter behind would be worse than not splicing at all.
- **Renaming does not transfer ownership — you end up with both.** The splice
    matches on `(class, member)` name. Rename a hand-owned member and it no
    longer has a counterpart, so it is kept and *appended*, while the original
    name is regenerated fresh from the manifest. The stub then carries your
    renamed copy **and** a regenerated member you thought you had replaced. If
    the intent was to override a generated member, keep its name; if it was to
    add a new one, delete the generated member's manifest entry.

______________________________________________________________________

## Typical workflow after scaffolding

1. Scaffold with state variables: `just-makeit new my_filter --object fir --state "coeffs:float[16]" --state "delay:float[16]"`
1. Open `native/inc/my_filter/fir/fir_core.h` — implement the inline `my_filter_fir_step()`.
1. Build and test: `make && make test`.
1. Add more state: `just-makeit add --object fir --state gain:float:1.0f` → **rebuilds** the object from the manifest, so keep your algorithm in the TOML `impl`/`create_impl` (or `git stash` first); the new field lands in the struct, constructor, getter/setter, and reset.
1. If you need a struct field that isn't a state variable (e.g. a scratch buffer), add it manually to the struct in `native/inc/my_filter/fir/fir_core.h` — the struct is sacred, so `jm apply` never re-renders it and your extra fields survive (a `jm add`/`jm regenerate` rebuild does re-stub the struct from the manifest, so re-add them after).

______________________________________________________________________

## 1. Declare your state variables upfront

Use `--state name:type[:default]` with `new --object` or with `object` so the
scaffolding matches your object from the start (`--state` describes the
object being created):

```sh
just-makeit new my_filter --object fir \
    --state cutoff_freq:float:440.0f \
    --state num_taps:int32_t:32
```

This generates the struct, constructor parameters, getter/setter pairs,
reset behaviour, and Python type stubs for each variable in one shot.

______________________________________________________________________

## 2. Add state variables to an existing object

```sh
just-makeit add --object fir --state drive:float:1.0f
```

Adding state is **structural**: `add` writes the new `[[fir.state]]`
entry to `just-makeit.toml`, then rebuilds the object from the manifest (a
delete-then-apply). The new field reaches the struct, constructor,
getter/setter, reset, and Python stub in one shot. Unlike `jm regenerate`
(which preserves hand-written bodies by default), `add` always discards the
sacred `_core.c` — the old body's signature predates the new field, so there's
nothing safe to splice back. Keep your algorithm in the TOML
`impl`/`create_impl` (the rebuild re-asserts it) or `git stash` first. `add`
prompts once before rebuilding; `--force` skips it.

Use this for any state variable that follows the standard lifecycle
(constructor parameter, getter/setter, reset target). Fixed-length arrays are
state too (`--state coeffs:float[64]`). For heap allocations, pointers or
nested structs, add them manually as described below.

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
See the [Multi-extension package](workflows/package.md) page for the full multi-object layout.

______________________________________________________________________

## 4. Implement `step`

Open `native/inc/<pkg>/<component>/<component>_core.h` (here
`native/inc/my_filter/fir/fir_core.h`) and replace the pass-through stub:

```c
static inline float _Complex
my_filter_fir_step(const my_filter_fir_state_t *state, float _Complex x)
{
    (void)state; /* TODO: implement using state variables */
    return (float _Complex)x;
}
```

Reads `state->cutoff_freq`, `state->num_taps`, etc. to process `x`. The
function is `static inline` in the header for maximum performance in the hot
path.

______________________________________________________________________

## 5. Add non-scalar state manually

For fields that don't fit the state pattern (heap allocations, pointers,
nested structs), add them directly to the struct in
`native/inc/<pkg>/<component>/<component>_core.h` — the struct is sacred, so
`jm apply` never re-renders it and your manual fields survive (a `jm add` /
`jm regenerate` rebuild re-stubs the struct from the manifest, so re-add them
after one):

```c
typedef struct {
    float cutoff_freq;
    int32_t num_taps;
    float *delay_line; /* add manually: allocated in create() */
} my_filter_fir_state_t;
```

Then implement any corresponding logic in `<component>_core.c`, and expose
new getters/setters with `jm property`.

______________________________________________________________________

## 6. Expose new Python methods

Declare the method and let jm write the binding and the stub:

```sh
just-makeit method fir gain_db --param db:float --return-type float
```

(or a `[[fir.methods]]` table and `jm apply`). jm injects the declaration
into `_core.h` and appends a stub to `_core.c` for you to fill in. A
property: `just-makeit property fir <name> --type float [--writable]`. A
binding the manifest cannot express belongs in a hook jm never writes —
`<component>_ext_extra.c` (standalone) or `<module>_ext_<obj>_extra.c`
(module), registered by an
[`[[<component>.extra_methods]]`](configuration.md#componentextra_methods-entries)
row, which renders its method-table row and its stub.

______________________________________________________________________

## 7. Add CTest tests

`native/tests/test_<component>_core.c` already has a template test. Add more
assertions inline, or register additional executables in
`native/src/<component>/<component>_extra.cmake`, which the generated
`CMakeLists.txt` includes and jm never writes (see
[Your own CMake](#your-own-cmake-dir_extracmake)):

```cmake
add_executable(test_fir_edge ${CMAKE_SOURCE_DIR}/native/tests/test_edge_cases.c)
target_link_libraries(test_fir_edge PRIVATE fir_core)
add_test(NAME test_fir_edge COMMAND test_fir_edge)
```

______________________________________________________________________

## 8. Add dependencies

Link a third-party library (FFTW, libsndfile, etc.) by declaring it in the
manifest, so every face — this build, the installed CMake package and the
`.pc` — gets it:

```toml
[project]
pkg_modules = ["fftw3f"]

[fir]
extra_link_libs = ["PkgConfig::FFTW3F"]
```

then `jm apply`. See
[When your library depends on another package](c-library.md#when-your-library-depends-on-another-package).
CMake the manifest cannot express goes in
`native/src/<component>/<component>_extra.cmake`, never the generated
`CMakeLists.txt`.

For Python runtime dependencies, add them to `pyproject.toml`:

```toml
[project]
dependencies = [
    "numpy",
    "scipy",
]
```
