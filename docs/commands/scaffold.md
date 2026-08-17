# Scaffold commands

These commands build the project structure. Run them once at the start of a
project, then use [Extend commands](extend.md) to add behaviour.

______________________________________________________________________

## `just-makeit new`

```text
just-makeit new <proj>
    [--object name ...]
    [--state name:type[:default] ...]
```

Start a new project. One command gives you a complete, building, tested C
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

By default, new projects use the [per-component fragment
layout](../declarative-scaffolding.md#three-layouts): the manifest carries
only `[project]` plus `include` globs, and each object/module routes to its
own `objects/<name>.toml` / `modules/<name>.toml`. Pass `--no-fragments` for
the legacy single-manifest layout where every section is inlined into
`just-makeit.toml`.

**Arguments**

| Argument                       | Description                                                                                                                                                                                                                                                                                              |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project`                      | Project name in `snake_case`. Used as the Python package name and distribution name.                                                                                                                                                                                                                     |
| `--object name`                | Scaffold a standalone object immediately. Repeatable.                                                                                                                                                                                                                                                    |
| `--module name`                | Scaffold an empty extension module immediately. Repeatable; mutually exclusive with `--object`.                                                                                                                                                                                                          |
| `--state name:type[:default]`  | Declare a state variable for the object. Repeatable.                                                                                                                                                                                                                                                     |
| `--arg-type TYPE`              | C type for `step()` input `x`. Defaults to `float _Complex`. Use `void` for generator objects with no scalar input. Append `[]` for objects whose primary operation takes a whole buffer: `--arg-type "float _Complex[]"`.                                                                               |
| `--return-type TYPE`           | C type for `step()` return value. Defaults to `--arg-type` for scalar inputs, or `void` for array inputs (`T[]`). Use `void` explicitly for sink objects that consume input but produce no scalar output.                                                                                                |
| `--build-system <cmake\|make>` | Build system to use. Default: `cmake`. Use `make` for a plain `Makefile` without CMake — useful for quick prototypes.                                                                                                                                                                                    |
| `--basic`                      | Deprecated alias for `--build-system make`.                                                                                                                                                                                                                                                              |
| `--perf`                       | Generate `jm_perf.h` with `JM_HOT`, `JM_LIKELY`, and `JM_FORCEINLINE` macros and apply them to `step()`. See [Performance annotations](../perf.md).                                                                                                                                                      |
| `--mutable`                    | Remove `const` from the state pointer in `step()`. Use for objects whose `step()` must mutate state directly (e.g. an NCO).                                                                                                                                                                              |
| `--no-state`                   | Scaffold the first object with no auto-generated state (see `just-makeit object`). Works in this one command. Mutually exclusive with `--state`.                                                                                                                                                         |
| `--no-step`                    | Scaffold the first object with no `step()`/`steps()` (see `just-makeit object`). Works in this one command.                                                                                                                                                                                              |
| `--no-reset`                   | Scaffold the first object with no `reset()` (see `just-makeit object`). Works in this one command.                                                                                                                                                                                                       |
| `--pytest`                     | Generate pure pytest tests instead of the default `unittest`-compatible shim.                                                                                                                                                                                                                            |
| `--pytest-benchmark`           | Generate `pytest-benchmark` bench files alongside the pytest tests.                                                                                                                                                                                                                                      |
| `--windows`                    | Target Windows too. Records `platforms = ["linux", "macos", "windows"]` in `[project]` and emits the MinGW runtime-DLL `if(WIN32 …)` block into each component/module `CMakeLists.txt`. Off by default (Linux/macOS only); a default project's `CMakeLists.txt` carries no `if(WIN32)` per-target block. |
| `--no-fragments`               | Use the legacy single-manifest layout: every object/module section is inlined into `just-makeit.toml` instead of routing to a `objects/<name>.toml` / `modules/<name>.toml` fragment.                                                                                                                    |
| `--fragments`                  | Deprecated no-op — the per-component fragment layout is the default now.                                                                                                                                                                                                                                 |
| `--find-package NAME`          | CMake `find_package(NAME REQUIRED)`. Repeatable. See [`extra_link_libs`/module dependencies](../configuration.md#module-dependencies-external-libraries).                                                                                                                                                |
| `--pkg-module NAME`            | pkg-config module linked via `pkg_check_modules`. Repeatable.                                                                                                                                                                                                                                            |
| `--c-dep DIR`                  | Vendored C subdirectory under `native/src/DIR` with no Python wrapper (`[project] c_deps`). Repeatable.                                                                                                                                                                                                  |
| `--c-style clang-format`       | Reformat generated C to the project's `.clang-format` style after every mutating command.                                                                                                                                                                                                                |

______________________________________________________________________

## `just-makeit module <name>`

Group multiple types into one `.so` subpackage. Creates the extension module
shell; types are added with `just-makeit object --module`. Must be run from
the project root.

```sh
just-makeit module filter
just-makeit module osc
```

The name may be **dotted** to nest the module in a subpackage — `just-makeit module dsp.filters` places the extension at `src/<pkg>/dsp/filters/` so it
imports as `from <pkg>.dsp.filters import Fir` (arbitrary depth supported).
Intermediate packages (`dsp`) get a plain `__init__.py`; the native sources
stay in a single flat dir (`native/src/dsp_filters/`). Objects are added the
same way: `just-makeit object fir --module dsp.filters`.

A module can also land its Python artifacts **inside an existing package**
instead of one named after itself, via the manifest-only `package` key
(there is no CLI flag — add it to `just-makeit.toml` and run `just-makeit apply`):

```toml
[module.wfm_reader]
objects = ["wfm_reader"]
package = "wfm"          # .so, .pyi, tests/ and benchmarks/ go to src/<pkg>/wfm/
```

`from <pkg>.wfm import Reader` then works alongside whatever `wfm` already
exports — the package's `__init__.py` gains the re-export rather than being
overwritten, and two modules may share one package. The C side is unaffected:
the sources stay in `native/src/wfm_reader/` and the `.so` is still
`wfm_reader`. The same key works for `capsule`, `composer` and `handle`
modules.

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

Types are added with `just-makeit object`. Module-level functions are added
with `just-makeit function`.

______________________________________________________________________

## `just-makeit object`

```text
just-makeit object <name>
    [--module <name>]
    [--state name:type[:default] ...]
    [--arg-type TYPE] [--return-type TYPE]
```

Add a Python type to the project — standalone or inside a module. Use this
when the algorithm needs persistent state (history, coefficients, a cursor, a
running accumulator). For stateless operations see [Stateful vs Pure](../pure.md).
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

| Argument                           | Description                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                             | Object name in `snake_case`. Becomes the C prefix and Python class name (title-cased).                                                                                                                                                                                                                                                                                                                                                      |
| `--module name`                    | Target module. Without this flag the object is standalone (own `.so`).                                                                                                                                                                                                                                                                                                                                                                      |
| `--state name:type[:default]`      | Declare a state variable. Repeatable.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `--arg-type TYPE`                  | C type for `step()` input. Defaults to `float _Complex`. Use `void` for generator objects with no input. Append `[]` for objects whose primary operation takes a whole buffer: `--arg-type "float _Complex[]"` — `steps()` is not generated.                                                                                                                                                                                                |
| `--return-type TYPE`               | C type for `step()` return value. Defaults to `--arg-type` for scalar inputs, or `void` for array inputs (`T[]`). Use `void` explicitly for sink objects that consume input but produce no output.                                                                                                                                                                                                                                          |
| `--perf`                           | Generate `jm_perf.h` and apply `JM_FORCEINLINE JM_HOT` to `step()`.                                                                                                                                                                                                                                                                                                                                                                         |
| `--mutable`                        | Remove `const` from the state pointer in `step()`. Use for objects whose `step()` must mutate state directly (e.g. an NCO that advances phase, a counter). The default `const` is correct for pure-computation objects; `--mutable` opts out.                                                                                                                                                                                               |
| `--no-state`                       | Suppress auto-generated state variables, constructor args, and getter/setter scaffolding. Emits `<<IMPLEMENT>>` stubs in the C struct body and lifecycle functions (`create`, `destroy`, `reset`). Mutually exclusive with `--state`. Use when the constructor signature is too domain-specific to express via `--state` (e.g. a filter that takes `const float *taps, size_t num_taps`).                                                   |
| `--no-step`                        | Suppress `step()` and `steps()` from all C and Python output. Lifecycle functions (`create`, `destroy`, `reset`) are still generated. Use for objects whose interface consists entirely of named methods added with `jm method`.                                                                                                                                                                                                            |
| `--no-reset`                       | Suppress `reset()` from all C and Python output: no binding, no `<obj>_reset()` declared or defined in the core, no `.pyi` entry, and nothing in the generated tests or benchmarks that calls it. Use when the object has nothing coherent to reset — a writer whose samples are already on disk, where a reset would corrupt the artifact rather than return the object to a clean state. Independent of `--no-state`. (gh-542)            |
| `--opaque-state`                   | Forward-declare the state struct in the public header and **define it in `_core.c`**, so its members stay private to your C and never become API (gh-588). Requires `--no-step` and excludes `--state` — jm cannot generate a `step()` over a struct it may not see, nor place fields in one it does not define. Reach for this when the struct is an implementation detail you intend to change.                                           |
| `--streamable`                     | Generate a `stream(block, *, count=None, on_block=None)` iterator and `__iter__`, so callers write `for blk in obj.stream(4096): ...` instead of a hand-rolled drain loop. Drives the object's `variable_output` method (blockwise) or built-in `steps` (a void-arg source); stops on a drained/empty block or after `count` blocks. `on_block(block)` fires **after** each block is consumed — the seam for pacing/back-pressure/progress. |
| `--stream-block N`                 | Default block size used by `__iter__` (and the `stream()` default). Implies `--streamable`.                                                                                                                                                                                                                                                                                                                                                 |
| `--async-stream`                   | Also generate `__aiter__` / `__anext__` so `async for blk in obj.stream(...)` (and `async for blk in obj`) work alongside the sync forms. `__anext__` runs each producer step in the running loop's default executor — pair with a `nogil` producer to actually free the loop during the kernel. Implies `--streamable`; off by default (no async glue unless asked).                                                                       |
| `--init-param name:type[:default]` | Declare a constructor-only parameter. Repeatable. Generates a typed constructor argument in both C and Python; not stored as a struct field, so it isn't backed by a getter/setter or restored on `reset()`. Composes with `--state` — init-params drive the ctor while `--state` fields stay internal, managed via `--impl create::...` (see [Types — file/socket reader](../types.md#file-socket-reader)).                                |
| `--impl file::funcname`            | Lift the `step()` body from `funcname` in `file` instead of emitting a blank `<<IMPLEMENT>>` stub. The body is extracted verbatim and `--replace` substitutions are applied before insertion.                                                                                                                                                                                                                                               |
| `--impl file::N:M`                 | Lift lines `N`..`M` (inclusive, 1-based) instead of a named function body. Out-of-bounds or inverted ranges error cleanly.                                                                                                                                                                                                                                                                                                                  |
| `--replace old::new`               | String substitution applied to the body lifted by `--impl`. Repeatable. Use to rename identifiers from the source file to match the generated struct/param names.                                                                                                                                                                                                                                                                           |
| `--class-name NAME`                | Override the auto-derived Python class name (e.g. `NCO` instead of `Nco`).                                                                                                                                                                                                                                                                                                                                                                  |

`jm object` also has a handful of rarer flags (`--serializable`,
`--step-delegates-to-steps`, `--variable-output`, `--array-arg`, …) covered
where they're relevant rather than here — see the
[CLI ↔ TOML mapping](../configuration.md#complete-cli-toml-mapping) for the
full list.

See [State Variable Types](../types.md) for supported types, defaults, and
C/Python mappings. `bool` is a usable scalar arg, return, and state type.

### `--preset NAME`

A preset is a shorthand for a common flag combination — nothing more. It
expands before the normal parser runs, so every preset is equivalent to
typing its flags out, and you can still pass any of them directly.

| Preset      | Expands to                                                       | Shape                                                 |
| ----------- | ---------------------------------------------------------------- | ----------------------------------------------------- |
| `processor` | (none)                                                           | Default: scalar in → scalar out.                      |
| `blockwise` | `--arg-type "float _Complex[]" --return-type "float _Complex[]"` | Array in → array out of the same length.              |
| `generator` | `--arg-type void`                                                | No input; produces output.                            |
| `consumer`  | `--return-type void`                                             | Consumes input; no output.                            |
| `reader`    | `--no-step --init-param filepath:const char *`                   | No auto-`step()`; add custom methods via `jm method`. |

```sh
just-makeit object osc --preset generator
```

`--preset` is not repeatable. The `blockwise` preset pairs an array input with
an array output of the same length; override the element types by passing your
own `--arg-type "T[]" --return-type "T[]"`.

Each `--state name:type[:default]` generates:

- A field in the C state struct
- A constructor parameter with the declared default
- `get_name()` and `set_name()` methods in both C and Python
- Getter/setter tests in both CTest and pytest
- Reset behaviour that restores the declared default

**Naming rules**

- ASCII letters, digits, and underscores only.
- Must not start with a digit.
- Examples: `engine`, `parser`, `rate_limiter`

Uppercase is accepted (a view's class name is `CamelCase` through the same
check), though object names are conventionally lowercase since the Python
class name is derived from them. Non-ASCII is rejected (gh-784): GCC accepts
UTF-8 identifiers as an extension and MSVC may not, so such a name compiles
on one toolchain and not another.

The Python class name is derived automatically:

| Object name        | Python class     |
| ------------------ | ---------------- |
| `engine`           | `Engine`         |
| `rate_limiter`     | `RateLimiter`    |
| `half_band_filter` | `HalfBandFilter` |

______________________________________________________________________

## `just-makeit add`

```text
just-makeit add --state name:type[:default] [...]
    [--object name]
```

Add one or more state variables to an existing standalone object. Must be run
from the project root.

```sh
just-makeit add --state order:int:4
just-makeit add --state threshold:double:0.5 --state window:int:64
just-makeit add --object parser --state depth:int:8
just-makeit add --param n_taps:int:16
```

When the project has a single standalone object `--object` may be omitted.

`add` accepts `--state` and `--param` (repeatable, mixable in one call).
Adding state is **structural**: `add` authors the new `[[obj.state]]` entries
into `just-makeit.toml`, then rebuilds the object from the manifest via the
regenerate path (delete + apply). The new fields reach the struct, the
constructor, the getter/setter, the reset target, and the Python stub in one
shot.

Because the rebuild deletes and re-stubs the sacred `_core.c`, any
hand-written `steps()`/lifecycle body is **discarded** unless it lives in the
TOML `impl`/`create_impl` (which the rebuild re-asserts) — keep your algorithm
there, or `git stash` first. `add` prompts for one confirmation before
rebuilding; `--force` skips it.

**Constraints**

- Each new variable name must be unique within the object's state list.
- Requires a `just-makeit.toml` — run `just-makeit new` first.
