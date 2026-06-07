# Configuration — just-makeit.toml

Every project scaffolded by `just-makeit new` contains a `just-makeit.toml`
file at the project root. It is the single source of truth for the project's
structure: what objects exist, what state they carry, what types and flags were
used, and how the build system is configured.

`just-makeit` reads this file before every `object`, `add`, `method`,
`property`, and `script` command — you never need to pass the project name or
repeat earlier choices on the command line.

______________________________________________________________________

## What is stored

| Category                                                          | Stored in TOML                     |
| ----------------------------------------------------------------- | ---------------------------------- |
| Project name and version                                          | Yes                                |
| Build system (`--build-system`)                                   | Yes                                |
| Performance annotations (`--perf`)                                | Yes                                |
| Test runner (`--pytest`, `--pytest-benchmark`)                    | Yes                                |
| Objects and their state variables                                 | Yes                                |
| `arg-type`, `return-type`, `--mutable`, `--no-state`, `--no-step` | Yes                                |
| Constructor parameters (`--init-param`)                           | Yes                                |
| Extra methods, properties, module-level functions                 | Yes                                |
| Module subpackage structure                                       | Yes                                |
| `--impl` / `--replace` lifted code                                | Yes — stored as `impl` / `replace` |

A lifted `--impl` body is stored in the manifest (as `impl`, `create_impl`,
`reset_impl`, `destroy_impl`, …), so `jm regenerate` and `jm apply` can
re-stamp it. Edits you make afterwards to the sacred `_core.c` are yours and
live only in source — the manifest holds the original lift, not your later
changes.

______________________________________________________________________

## Project layout and schema

After `just-makeit new my_project` followed by `just-makeit object engine`.
`just-makeit.toml` sits at the project root — every command reads it from
there, no flags required.

=== "File tree"

````
```
my_project/
├── just-makeit.toml
├── CMakeLists.txt
├── Makefile
├── pyproject.toml
├── cmake/
│   ├── my_project-config.cmake.in
│   └── my-project.pc.in
├── native/
│   ├── inc/
│   │   ├── my_project.h
│   │   ├── clib_common.h
│   │   ├── pyex_common.h
│   │   └── engine/
│   │       └── engine_core.h
│   ├── src/
│   │   ├── my_project_lib.c
│   │   └── engine/
│   │       ├── engine_core.c
│   │       ├── engine_ext.c
│   │       └── CMakeLists.txt
│   ├── tests/
│   │   └── test_engine_core.c
│   └── benchmarks/
│       └── bench_engine_core.c
└── src/
    └── my_project/
        ├── __init__.py
        ├── engine.pyi
        ├── tests/
        │   └── test_engine.py
        └── benchmarks/
            └── bench_engine.py
```
````

=== "just-makeit.toml"

````
```toml
[project]
name             = "my_project"
version          = "0.1.0"
build            = "cmake"
perf             = "false"
pytest           = "false"
pytest_benchmark = "false"

# One section per object, named after the object.
[engine]
arg_type    = "float _Complex"
return_type = "float _Complex"
mutable     = "false"
no_state    = "false"
no_step     = "false"

# One entry per --state declaration.
[[engine.state]]
name    = "gain"
type    = "double"
default = "1.0"

# One entry per --init-param.
[[engine.init_params]]
name    = "order"
type    = "int"
default = "4"

# One entry per --array-arg.
[[engine.array_args]]
name = "coeffs"
type = "float32"

# One entry per `just-makeit method`.
[[engine.methods]]
name        = "normalize"
return_type = "void"
params      = [{name = "scale", type = "double"}]

# One entry per `just-makeit property`.
[[engine.properties]]
name     = "peak"
type     = "double"
writable = true
field    = true

# Module subpackage, named after the module.
[module.filter]
objects = ["fir", "biquad"]

[[module.filter.functions]]
name        = "design_lowpass"
return_type = "void"
doc         = "Compute FIR coefficients for a lowpass filter."
params      = [{name = "cutoff", type = "double"}]

[fir]
arg_type    = "float _Complex"
return_type = "float _Complex"
mutable     = "false"
no_state    = "false"
no_step     = "false"

[[fir.state]]
name    = "coeffs"
type    = "float[16]"
default = "0.0f"
```
````

______________________________________________________________________

## Module dependencies & external libraries

`jm apply` regenerates each module's `CMakeLists.txt`, so dependency wiring must
be **declared in the manifest** — a hand-edited link line is clobbered on the
next apply. Four keys cover every case; none of them require post-apply patching,
and `jm status --check` stays clean with no allowlist.

| You need…                                                        | Key                                       | Scope          | Effect                                                                                                    |
| ---------------------------------------------------------------- | ----------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------------- |
| link an external/system library                                  | `extra_link_libs`                         | `[module.<m>]` | appended verbatim to `target_link_libraries`; **generator expressions allowed**                           |
| link a *sibling* module's core                                   | `extra_link_libs` (name the `<obj>_core`) | `[module.<m>]` | names the target on the link line                                                                         |
| call into another object's API (link **and** include its header) | `depends_on`                              | `[<object>]`   | links `<dep>_core` **and** injects `#include "<dep>/<dep>_core.h"` (gh-170)                               |
| add a hand-written **C support dir** other modules link          | `c_deps`                                  | `[project]`    | emits `add_subdirectory(native/src/<dir>)`; that dir's `CMakeLists.txt` is hand-owned (never regenerated) |
| add a hand-written **Python extension module**                   | `no_generate`                             | `[module.<m>]` | emits the `add_subdirectory`, leaves the module's `_ext.c` / `.pyi` / CMake alone                         |

### `extra_link_libs` — external and sibling libraries

```toml
[module.source]
objects = ["nco", "lo", "awgn"]
# system libs and generator expressions are fine:
extra_link_libs = ["$<$<PLATFORM_ID:Linux>:mvec>", "${MY_STATIC_LIB}", "m"]

[module.wfm]
objects = ["waveform_engine"]
# link sibling cores from other modules by their <obj>_core target name:
extra_link_libs = ["source_core", "lfsr_core", "m"]
```

Set it from the CLI with `jm module <m> --extra-link-libs TARGET` (repeatable).

### `depends_on` — link *and* include a dependency

When an object actually *calls* another object's C API (not just links it), use
`depends_on` on the **object**, not the module. It links the dependency's core
**and** auto-includes its header, so opaque-typed fields compile:

```toml
[waveform_engine]
depends_on = ["source", "lfsr"]   # links source_core/lfsr_core + includes both headers
```

Prefer this over naming the cores in `extra_link_libs` whenever the dependency's
*types or functions* are referenced from your `_core.c`.

### `c_deps` — hand-written C support directories

For a pure-C directory (object libraries, vendored code) that has **no** Python
binding but that modules link against:

```toml
[project]
c_deps = ["io", "vendor_dsp"]   # add_subdirectory(native/src/io), …
```

Each listed dir owns its `CMakeLists.txt` (define `add_library(<name>_core …)`,
tests, etc.); modules then link it via `extra_link_libs = ["io_core", …]`. CLI:
`jm new --c-dep DIR` (repeatable).

### `no_generate` — hand-written extension modules

For a module whose binding is written by hand (e.g. a free-function API over an
opaque capsule):

```toml
[module.io]
no_generate = "true"
```

`jm apply` emits the `add_subdirectory(native/src/io)` and otherwise leaves the
module untouched — `io_ext.c`, `io.pyi`, and its `CMakeLists.txt` are yours.
Pair with [`reexports`](#modulename-keys) on a sibling module to fold its symbols
into a generated package `__init__.py`.

### Worked example: three interdependent modules + a C support dir

```toml
[project]
c_deps = ["io"]                    # hand-written native/src/io (io_core, …)

[module.source]
objects = ["source", "lfsr"]
extra_link_libs = ["${DOPPLER_STATIC_LIBRARY}", "io_core", "m"]

[module.wfm]
objects = ["waveform_engine"]
extra_link_libs = ["source_core", "lfsr_core", "m"]
# …or, if waveform_engine calls source/lfsr APIs, drop those two libs and use:
#   [waveform_engine]
#   depends_on = ["source", "lfsr"]
```

This is exactly the wiring doppler uses (`c_deps = ["hbdecim", "resamp", "wfmcompose"]`; `[module.ddc] extra_link_libs = ["lo_core", …]`;
`[synth] depends_on = ["lo", "awgn", "pn"]`) — fully declarative, idempotent
across `jm apply`.

______________________________________________________________________

## Complete CLI ↔ TOML mapping

Every TOML key the schema accepts maps to a CLI flag. This is the
Phase 2 acceptance bar from the
[implementation plan](developers/implementation-plan.md): no feature
should require a TOML edit before it can be used.

Status legend: ✅ on main · 🟡 CLI flag pending (TOML works today).

> CLI and TOML are both first-class authoring paths. The CLI is the
> recommended way (presets, validators, errors); TOML editing is a
> fully supported alternative for power users or for knobs the CLI
> hasn't yet exposed. The 🟡 rows below are TOML-only today and are
> tracked for CLI parity — but unlike previous phrasing, this is no
> longer "by design"; the goal is parity.

### `[project]` keys

| TOML key           | CLI flag                                  | Status       | Notes                                  |
| ------------------ | ----------------------------------------- | ------------ | -------------------------------------- |
| `name`             | `jm new <NAME>`                           | ✅           | Required positional.                   |
| `version`          | `jm config version X`                     | ✅           | Bumped by `jm app` / release tooling.  |
| `build`            | `jm new --build-system cmake\|make`       | ✅           |                                        |
| `perf`             | `jm new --perf` / `jm perf`               | ✅           | Retrofit available via `jm perf`.      |
| `pytest`           | `jm new --pytest`                         | ✅           |                                        |
| `pytest_benchmark` | `jm new --pytest-benchmark`               | ✅           |                                        |
| `find_packages`    | `jm new --find-package NAME` (repeatable) | ✅ (0.13.23) | CMake `find_package(NAME REQUIRED)`.   |
| `pkg_modules`      | `jm new --pkg-module NAME` (repeatable)   | ✅ (0.13.23) | pkg-config via `pkg_check_modules`.    |
| `c_deps`           | `jm new --c-dep DIR` (repeatable)         | ✅ (0.13.23) | Vendored C subdir (no Python wrapper). |
| `schema`           | (managed by `jm upgrade`)                 | ✅           | Migrated; no user-facing flag.         |

### `[<component>]` keys

| TOML key             | CLI flag                                          | Status       | Notes                                                                                   |
| -------------------- | ------------------------------------------------- | ------------ | --------------------------------------------------------------------------------------- |
| `arg_type`           | `jm object --arg-type T`                          | ✅           |                                                                                         |
| `return_type`        | `jm object --return-type T`                       | ✅           |                                                                                         |
| `mutable`            | `jm object --mutable`                             | ✅           |                                                                                         |
| `no_state`           | `jm object --no-state`                            | ✅           |                                                                                         |
| `no_step`            | `jm object --no-step`                             | ✅           |                                                                                         |
| `class_name`         | `jm object --class-name NAME`                     | ✅           |                                                                                         |
| `depends_on`         | (inferred from `--module`)                        | ✅           | Set automatically when an object lives in a module.                                     |
| `extra_link_libs`    | (component scope: TOML only)                      | 🟡           | Per-module is `jm module --extra-link-libs`; per-component still TOML-only (rare case). |
| `extra_include_dirs` | `jm object --extra-include-dirs DIR` (repeatable) | ✅ (0.13.23) |                                                                                         |

### `[[<component>.state]]` entries

| TOML field                         | CLI flag                                             | Status |
| ---------------------------------- | ---------------------------------------------------- | ------ |
| `name`, `type`, `default`          | `jm object --state name:type[:default]` (repeatable) | ✅     |
| `name`, `type`, `opaque = true`    | (TOML only)                                          | 🟡     |
| `name`, `type`, `no_ctor = true`   | (TOML only)                                          | 🟡     |
| `name`, `type`, `roles = "config"` | (TOML only)                                          | 🟡     |

The three rare modifiers (`opaque`, `no_ctor`, `roles`) currently
require editing `just-makeit.toml` directly. CLI flags are pending
(syntax under discussion: `--state name:type:opaque`,
`--state name:type:no-ctor`, `--state name:type:role=config`). Until
those land, hand-editing the manifest is the workaround.

### `[[<component>.init_params]]` entries

| TOML field                                                | CLI flag                                                  | Status                      |
| --------------------------------------------------------- | --------------------------------------------------------- | --------------------------- |
| `name`, `type`, `default`                                 | `jm object --init-param name:type[:default]` (repeatable) | ✅                          |
| `optional = true`                                         | `jm object --init-param 'name:type[]:optional'`           | ✅ (syntax extension)       |
| `default_raw`, `real_type`, `real_create_fn`, `create_fn` | (TOML only)                                               | 🟡                          |
| compose with `[[state]]`                                  | `--init-param + --state` together                         | ✅ (0.13.23) (gate dropped) |

### `[[<component>.methods]]` entries

| TOML field                        | CLI flag                                                | Status       |
| --------------------------------- | ------------------------------------------------------- | ------------ |
| `name`, `arg_type`, `return_type` | `jm method <obj> <method> --arg-type T --return-type T` | ✅           |
| `params = [{name, type}]`         | `jm method --param name:type` (repeatable)              | ✅           |
| `extra_args = [{name, type}]`     | `jm method --extra-arg name:type` (alias for `params`)  | ✅ (0.14.2)  |
| `variable_output = true`          | `jm method --variable-output`                           | ✅           |
| `pass_capacity = true`            | `jm method --pass-capacity`                             | ✅ (0.14.4)  |
| `nogil = true`                    | `jm method --nogil`                                     | ✅ (0.15.2)  |
| `max_out = N` (sibling stub)      | `jm method --max-out N`                                 | ✅ (0.13.23) |
| `multi_output = ["T", ...]`       | `jm method --multi-output T` (repeatable)               | ✅           |
| `out_type = "T"`                  | `jm method --out-type T`                                | ✅           |
| `out_divisor = N`                 | `jm method --out-divisor N`                             | ✅           |
| `batch = true`                    | `jm method --batch`                                     | ✅           |
| `bench = false`                   | `jm method --no-bench`                                  | ✅           |
| `result_fields = [{name, type}]`  | `jm method --result-field name:type` (repeatable)       | ✅ (0.13.23) |
| `max_results = N`                 | (TOML only; default 64)                                 | 🟡           |
| `py_return_type = "..."`          | `jm method --py-return-type STR`                        | ✅           |
| `impl = "..."` body               | `jm method --impl file::funcname`                       | ✅           |

### `[[<component>.properties]]` entries

| TOML field                                      | CLI flag                            | Status |
| ----------------------------------------------- | ----------------------------------- | ------ |
| `name`, `type`                                  | `jm property <obj> <prop> --type T` | ✅     |
| `writable = true`                               | `jm property --writable`            | ✅     |
| `field = true`                                  | `jm property --field`               | ✅     |
| `buf_field`, `len_field`, `valid_field`, `expr` | (TOML only)                         | 🟡     |

### `[<component>]` lifecycle impl bodies

| TOML field                     | CLI flag                                   | Status       |
| ------------------------------ | ------------------------------------------ | ------------ |
| `impl = "..."` (step body)     | `jm object --impl file::funcname`          | ✅           |
| `impl_file = "path::N:M"`      | `jm object --impl file::N:M` (line range)  | ✅ (0.14)    |
| `create_impl = "..."`          | `jm object --impl create::file::funcname`  | ✅ (0.13.23) |
| `reset_impl = "..."`           | `jm object --impl reset::file::funcname`   | ✅ (0.13.23) |
| `destroy_impl = "..."`         | `jm object --impl destroy::file::funcname` | ✅ (0.13.23) |
| `init_post_parse_impl = "..."` | (TOML only)                                | 🟡           |

The `--impl file::N:M` form lifts source lines `N..M` (inclusive, 1-based)
instead of a named function body; it composes with the slot prefixes
(`create::file::N:M`) and out-of-bounds ranges error cleanly.

### `[module.<name>]` keys

| TOML key                              | CLI flag                                          | Status       |
| ------------------------------------- | ------------------------------------------------- | ------------ |
| `objects` (list)                      | (auto-populated by `jm object --module <mod>`)    | ✅           |
| `extra_link_libs`                     | `jm module --extra-link-libs TARGET` (repeatable) | ✅ (0.13.23) |
| `extra_include_dirs`                  | `jm module --extra-include-dirs DIR` (repeatable) | ✅ (0.13.23) |
| `extra_types`                         | `jm module --extra-types NAME` (repeatable)       | ✅ (0.13.23) |
| `no_generate = "true"`                | (TOML only)                                       | 🟡           |
| `functions`                           | (auto-populated by `jm function --module <mod>`)  | ✅           |
| `reexports = { sub = ["name", ...] }` | (TOML only)                                       | 🟡 (0.15.1)  |

`reexports` folds names from a *sibling* extension (typically a `no_generate`
module whose binding/`.pyi` are hand-written) into this module's generated
`__init__.py` — both the import block and `__all__` — so the re-export glue
regenerates from the manifest instead of being a hand-edit `jm apply` would
clobber. Output is single-line, matching the rest of the package.

### `[[module.<name>.functions]]` entries

| TOML field                       | CLI flag                                                    | Status       |
| -------------------------------- | ----------------------------------------------------------- | ------------ |
| `name`, `return_type`, `doc`     | `jm function <fn> --module <mod> --return-type T --doc STR` | ✅           |
| `params = [{name, type, out?}]`  | `jm function --param name:T` + `--out-param name:T[]`       | ✅ (0.13.22) |
| `params … {mutable = true}`      | (synonym for `out` — writable array param)                  | ✅ (0.15.3)  |
| `inline = true`                  | `jm function --inline`                                      | ✅           |
| `out_type = "T"`                 | `jm function --out-type T`                                  | ✅ (0.13.23) |
| `result_fields = [{name, type}]` | `jm function --result-field name:type` (repeatable)         | ✅ (0.13.23) |
| `max_results_param`              | (TOML only)                                                 | 🟡           |
| `impl = "..."` body              | `jm function --impl file::funcname`                         | ✅           |

### Counts

- **✅ on main**: ~66 keys (every common path; Phase 2 stack shipped in 0.13.23)
- **🟡 CLI flag pending**: 15 keys — rare modifiers (`opaque`, `no_ctor`, `roles`, `buf_field`/`expr` property variants, `init_post_parse_impl`, `default_raw`/`real_type` init-param details, `no_generate` module, `max_results` / `max_results_param`). These are foot-guns to close: TOML is the persistence layer, not the user interface. Each will get a CLI flag in Phase 3.

Phase 2 acceptance bar — "every TOML field has a 'Reachable via CLI' column ✓" — is met for the common path. The remaining 🟡 rows are tracked Phase 3 work, not by-design exceptions.

______________________________________________________________________

## Schema reference

### `[project]`

| Key                | Type                  | Default   | Set by                                             |
| ------------------ | --------------------- | --------- | -------------------------------------------------- |
| `name`             | string                | —         | `just-makeit new <name>`                           |
| `version`          | string                | `"0.1.0"` | `just-makeit new` / `just-makeit config version X` |
| `build`            | `"cmake"` or `"make"` | `"cmake"` | `--build-system make`                              |
| `perf`             | `"true"` or `"false"` | `"false"` | `--perf`                                           |
| `pytest`           | `"true"` or `"false"` | `"false"` | `--pytest`                                         |
| `pytest_benchmark` | `"true"` or `"false"` | `"false"` | `--pytest-benchmark`                               |

### `[<object>]`

One section per standalone object or module-member object. The section name
is whatever you passed to `just-makeit object <name>`.

| Key           | Type                  | Default            | Set by          |
| ------------- | --------------------- | ------------------ | --------------- |
| `arg_type`    | string                | `"float _Complex"` | `--arg-type`    |
| `return_type` | string                | same as `arg_type` | `--return-type` |
| `mutable`     | `"true"` or `"false"` | `"false"`          | `--mutable`     |
| `no_state`    | `"true"` or `"false"` | `"false"`          | `--no-state`    |
| `no_step`     | `"true"` or `"false"` | `"false"`          | `--no-step`     |

### `[[<object>.state]]`

One entry per `--state` declaration.

| Key       | Type   | Notes                                 |
| --------- | ------ | ------------------------------------- |
| `name`    | string | Valid C identifier                    |
| `type`    | string | C type; append `[N]` for fixed arrays |
| `default` | string | C initialiser expression              |

### `[[<object>.array_args]]`

Fixed-size array constructor arguments added with `--array-arg`.

| Key    | Type   | Notes                                                                                                                                                         |
| ------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name` | string | Argument name                                                                                                                                                 |
| `type` | string | Stored as NumPy dtype name (`float32`, `float64`, `complex64`, …); C types (`float`, `double`, `float _Complex`, …) are also accepted on input and normalised |

### `[[<object>.init_params]]`

Constructor-only parameters added with `--init-param` (no getter/setter, no
reset). Same `name` / `type` / `default` keys as `state`.

### `[[<object>.methods]]`

One entry per `just-makeit method` call.

| Key               | Type                    | Notes                                                     |
| ----------------- | ----------------------- | --------------------------------------------------------- |
| `name`            | string                  | Method name                                               |
| `arg_type`        | string                  | Array-style input type                                    |
| `return_type`     | string                  | C return type                                             |
| `params`          | array of `{name, type}` | Named scalar / array parameters                           |
| `variable_output` | bool                    | `--variable-output`                                       |
| `pass_capacity`   | bool                    | `--pass-capacity` (5-arg `(…, out, max_out)` C form)      |
| `nogil`           | bool                    | `--nogil` (release the GIL across the kernel; see below)  |
| `batch`           | bool                    | `--batch`                                                 |
| `multi_output`    | array of strings        | `--multi-output` types                                    |
| `out_type`        | string                  | `--out-type`                                              |
| `out_divisor`     | int                     | `--out-divisor` (default `1`; omitted from TOML when `1`) |

`nogil` wraps the pure-C kernel of a `variable_output` execute method in
`Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS` (numpy accessors hoisted out
first), so a thread-per-shard worker — one object + output buffer per thread —
scales across cores instead of serialising on the GIL. Opt-in: it is sound
only when the object is not shared across threads concurrently (one object per
stream).

### `[[<object>.properties]]`

One entry per `just-makeit property` call.

| Key        | Type   | Notes                                                  |
| ---------- | ------ | ------------------------------------------------------ |
| `name`     | string | Property name                                          |
| `type`     | string | C type of the value                                    |
| `writable` | bool   | `--writable`                                           |
| `field`    | bool   | `--field` (adds struct member, auto-implements getter) |

### `[module.<name>]`

| Key                                                      | Type                    | Notes                                                                   |
| -------------------------------------------------------- | ----------------------- | ----------------------------------------------------------------------- |
| `objects`                                                | array of strings        | Objects in declaration order                                            |
| `functions`                                              | array                   | Module-level functions (see below)                                      |
| `reexports`                                              | table `{sub = [names]}` | Re-export sibling symbols into `__init__.py` (0.15.1)                   |
| `extra_link_libs` / `extra_include_dirs` / `extra_types` | array                   | Extra CMake wiring                                                      |
| `no_generate`                                            | string `"true"`         | Hand-written module: `jm apply` only wires the CMake `add_subdirectory` |

### `[[module.<name>.functions]]`

One entry per `just-makeit function` call.

| Key           | Type                          | Notes                                                                                                                                 |
| ------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | string                        | Function name                                                                                                                         |
| `return_type` | string                        | C return type                                                                                                                         |
| `doc`         | string                        | Python docstring                                                                                                                      |
| `params`      | array of `{name, type, out?}` | Parameters; an array param with `out = true` (or its synonym `mutable = true`) is writable — generated `T *name`, not `const T *name` |

______________________________________________________________________

## Inspecting config

```sh
just-makeit config
```

Prints a summary of the project and every object's state variables:

```
project:  my_project
version:  0.1.0

engine:
  gain:          double = 1.0
  center_freq:   double = 1000.0
```

To update the version:

```sh
just-makeit config version 0.2.0
```

______________________________________________________________________

## Reconstructing a project

`just-makeit script` reads `just-makeit.toml` and prints the exact sequence of
CLI commands that would recreate the project from scratch:

```sh
just-makeit script          # print to stdout
just-makeit script | sh     # pipe directly into a shell to rebuild
```

Example output for a two-object project:

```sh
#!/usr/bin/env sh
# Reconstructed from just-makeit.toml

just-makeit new my_project

cd my_project

just-makeit object engine \
    --state "gain:double:1.0" \
    --state "center_freq:double:1000.0"

just-makeit object detector \
    --arg-type "float _Complex" \
    --state "threshold:float:0.5f"
```

### When is this useful?

**Moving a project.** Copy `just-makeit.toml` to a new machine, run
`just-makeit script | sh`, and the full scaffold is regenerated. Your own
code (business logic in `*_core.c`, tests, customisations) travels with the
project directory as normal; the script just recreates the generated
boilerplate if it was ever lost or corrupted.

**Starting fresh after a breaking change.** If a just-makeit update changes
generated file layouts, `just-makeit script | sh` in an empty directory
produces a clean scaffold at the current version.

**Documentation / reproducibility.** Commit `just-makeit.toml` to record
exactly how the project was built. Anyone can reproduce the generated
structure without needing to remember the original command sequence.

!!! note

    The original `--impl` / `--replace` lift **is** stored in the manifest
    (`impl`, `replace`, …), so `script | sh` reproduces it. Any edits you
    made to the sacred `_core.c` afterwards live only in source — keep that
    in version control.

______________________________________________________________________

## Editing TOML by hand

The file is plain TOML — you can edit it directly. `just-makeit` will read
your changes on the next command. The rules:

- **Order matters for state variables**: `[[<object>.state]]` entries are
    emitted in the order they appear, which controls constructor argument order
    in both C and Python.
- **Keys must come before sub-table arrays**: all scalar keys on an object
    section (`impl`, `create_impl`, `reset_impl`, `destroy_impl`, `arg_type`, `mutable`, …)
    must appear **before** the first `[[<object>.state]]` or
    `[[<object>.methods]]` entry. TOML parses bare keys after an
    array-of-tables header as part of that entry, not the parent section,
    so keys placed after a `[[…]]` line are silently dropped by the parser.
- **Editing a signature** in TOML (removing a state variable, changing a
    method's return type) propagates to the glue files (`_ext.c`, `.pyi`,
    `CMakeLists.txt`) and the public declarations in `_core.h` on the next
    command, but the sacred `_core.c` body is left as you wrote it. Run
    `jm regenerate <obj>` to rebuild that component cleanly from the manifest
    (this discards the `_core.c` body — `git stash` first).
- **Don't rename the file** — `just-makeit` always looks for `just-makeit.toml`
    at the project root.
