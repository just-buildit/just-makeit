# Scaffold commands

These commands build the project structure.  Run them once at the start of a
project, then use [Extend commands](extend.md) to add behaviour.

______________________________________________________________________

## `just-makeit new <proj> [--object name ...] [--state name:type[:default] ...]`

Start a new project.  One command gives you a complete, building, tested C
extension project — optionally with a first object already scaffolded.

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

| Argument                       | Description                                                                                                                                                                                                                |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project`                      | Project name in `snake_case`. Used as the Python package name and distribution name.                                                                                                                                       |
| `--object name`                | Scaffold a standalone object immediately. Repeatable.                                                                                                                                                                      |
| `--module name`                | Scaffold an empty extension module immediately. Repeatable; mutually exclusive with `--object`.                                                                                                                            |
| `--state name:type[:default]`  | Declare a state variable for the object. Repeatable.                                                                                                                                                                       |
| `--arg-type TYPE`              | C type for `step()` input `x`. Defaults to `float _Complex`. Use `void` for generator objects with no scalar input. Append `[]` for objects whose primary operation takes a whole buffer: `--arg-type "float _Complex[]"`. |
| `--return-type TYPE`           | C type for `step()` return value. Defaults to `--arg-type` for scalar inputs, or `void` for array inputs (`T[]`). Use `void` explicitly for sink objects that consume input but produce no scalar output.                  |
| `--build-system <cmake\|make>` | Build system to use. Default: `cmake`. Use `make` for a plain `Makefile` without CMake — useful for quick prototypes.                                                                                                      |
| `--basic`                      | Deprecated alias for `--build-system make`.                                                                                                                                                                                |
| `--perf`                       | Generate `jm_perf.h` with `JM_HOT`, `JM_LIKELY`, and `JM_FORCEINLINE` macros and apply them to `step()`. See [Performance annotations](../perf.md).                                                                       |
| `--mutable`                    | Remove `const` from the state pointer in `step()`. Use for objects whose `step()` must mutate state directly (e.g. an NCO).                                                                                                |
| `--pytest`                     | Generate pure pytest tests instead of the default `unittest`-compatible shim.                                                                                                                                              |
| `--pytest-benchmark`           | Generate `pytest-benchmark` bench files alongside the pytest tests.                                                                                                                                                        |

______________________________________________________________________

## `just-makeit module <name>`

Group multiple types into one `.so` subpackage.  Creates the extension module
shell; types are added with `just-makeit object --module`.  Must be run from
the project root.

```sh
just-makeit module filter
just-makeit module osc
```

Creates:

| File                               | Purpose                                            |
| ---------------------------------- | -------------------------------------------------- |
| `native/inc/<name>/<name>_core.h`  | Public C API — declare module-level functions here |
| `native/src/<name>/<name>_core.c`  | Implementation — write module-level functions here |
| `native/src/<name>/<name>_ext.c`   | Python binding (auto-generated)                    |
| `native/src/<name>/CMakeLists.txt` | Python module target                               |
| `src/<pkg>/<name>/__init__.py`     | Subpackage init (empty exports)                    |

Appends `add_subdirectory(native/src/<name>)` to the root `CMakeLists.txt`
and records `[module.<name>]` with an empty `objects` list in `just-makeit.toml`.

Types are added with `just-makeit object`.  Module-level functions are added
with `just-makeit function`.

______________________________________________________________________

## `just-makeit object <name> [--module <name>] [--state name:type[:default] ...] [--arg-type TYPE] [--return-type TYPE]`

Add a Python type to the project — standalone or inside a module.  Use this
when the algorithm needs persistent state (history, coefficients, a cursor, a
running accumulator).  For stateless operations see [Stateful vs Pure](../pure.md).
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

| File                                   | Purpose                                         |
| -------------------------------------- | ----------------------------------------------- |
| `native/inc/<obj>/<obj>_core.h`        | Header: struct, inline `_step`, getters/setters |
| `native/src/<obj>/<obj>_core.c`        | Source: create/destroy/reset/steps              |
| `native/src/<obj>/CMakeLists.txt`      | OBJECT library + C test + bench                 |
| `native/tests/test_<obj>_core.c`       | C test with `CHECK` macro counter               |
| `native/benchmarks/bench_<obj>_core.c` | C benchmark                                     |

**Additional files for standalone objects** (no `--module`):

| File                            | Purpose                        |
| ------------------------------- | ------------------------------ |
| `native/src/<obj>/<obj>_ext.c`  | Python C extension (own `.so`) |
| `src/<pkg>/<obj>.pyi`           | Type stub                      |
| `src/<pkg>/tests/test_<obj>.py` | pytest suite                   |

**Module files regenerated** after each `just-makeit object --module`:

| File                                 | What changes                                   |
| ------------------------------------ | ---------------------------------------------- |
| `native/src/<module>/<module>_ext.c` | New type block added; `PyMODINIT_FUNC` updated |
| `native/src/<module>/CMakeLists.txt` | New `<obj>_core` added to link list            |
| `src/<pkg>/<module>/__init__.py`     | New type added to import and `__all__`         |
| `src/<pkg>/<module>/<module>.pyi`    | Type stub regenerated with new type            |

**Per-object files created for module objects:**

| File                                           | Purpose                                               |
| ---------------------------------------------- | ----------------------------------------------------- |
| `src/<pkg>/<module>/tests/test_<obj>.py`       | pytest suite (API, getters/setters, reset, lifecycle) |
| `src/<pkg>/<module>/benchmarks/bench_<obj>.py` | pytest-benchmark throughput suite                     |

The module `_ext.c` is always fully regenerated from the complete object list
— never patched — so adding a third type never disturbs the first two.

**Arguments**

| Argument                           | Description                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                             | Object name in `snake_case`. Becomes the C prefix and Python class name (title-cased).                                                                                                                                                                                                                                                                                                    |
| `--module name`                    | Target module. Without this flag the object is standalone (own `.so`).                                                                                                                                                                                                                                                                                                                    |
| `--state name:type[:default]`      | Declare a state variable. Repeatable.                                                                                                                                                                                                                                                                                                                                                     |
| `--arg-type TYPE`                  | C type for `step()` input. Defaults to `float _Complex`. Use `void` for generator objects with no input. Append `[]` for objects whose primary operation takes a whole buffer: `--arg-type "float _Complex[]"` — `steps()` is not generated.                                                                                                                                              |
| `--return-type TYPE`               | C type for `step()` return value. Defaults to `--arg-type` for scalar inputs, or `void` for array inputs (`T[]`). Use `void` explicitly for sink objects that consume input but produce no output.                                                                                                                                                                                        |
| `--perf`                           | Generate `jm_perf.h` and apply `JM_FORCEINLINE JM_HOT` to `step()`.                                                                                                                                                                                                                                                                                                                       |
| `--mutable`                        | Remove `const` from the state pointer in `step()`. Use for objects whose `step()` must mutate state directly (e.g. an NCO that advances phase, a counter). The default `const` is correct for pure-computation objects; `--mutable` opts out.                                                                                                                                             |
| `--no-state`                       | Suppress auto-generated state variables, constructor args, and getter/setter scaffolding. Emits `<<IMPLEMENT>>` stubs in the C struct body and lifecycle functions (`create`, `destroy`, `reset`). Mutually exclusive with `--state`. Use when the constructor signature is too domain-specific to express via `--state` (e.g. a filter that takes `const float *taps, size_t num_taps`). |
| `--no-step`                        | Suppress `step()` and `steps()` from all C and Python output. Lifecycle functions (`create`, `destroy`, `reset`) are still generated. Use for objects whose interface consists entirely of named methods added with `jm method`.                                                                                                                                                          |
| `--init-param name:type[:default]` | Declare a constructor parameter for `--no-state` objects. Repeatable. Generates a typed constructor argument in both C and Python; not stored as a struct field. Requires `--no-state`.                                                                                                                                                                                                   |
| `--impl file::funcname`            | Lift the `step()` body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub. The function body is extracted verbatim and substitutions from `--replace` are applied before insertion.                                                                                                                                                                               |
| `--replace old::new`               | String substitution applied to the body lifted by `--impl`. Repeatable. Use to rename identifiers from the source file to match the generated struct/param names.                                                                                                                                                                                                                         |

See [State Variable Types](../types.md) for supported types, defaults, and C/Python mappings.

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

## `just-makeit add --state name:type[:default] [...] [--object name]`

Add one or more state variables to an existing standalone object. Must be run
from the project root.

```sh
just-makeit add --state order:int:4
just-makeit add --state threshold:double:0.5 --state window:int:64
just-makeit add --object parser --state depth:int:8
just-makeit add --param n_taps:int:16
```

When the project has a single standalone object `--object` may be omitted.

`add` accepts `--state` and `--param` (repeatable, mixable in one call) and
regenerates the six state-sensitive files from the merged state list:

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
