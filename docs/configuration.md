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

By default (the **fragment layout** — see [`jm new`](commands/scaffold.md)),
`just-makeit.toml` itself holds only `[project]` plus an `include` glob; each
object's and module's section lives in its own `objects/<name>.toml` /
`modules/<name>.toml` fragment instead of being inlined. The schema — every
key and table shown below — is identical either way; only which physical file
holds it changes. Pass `--no-fragments` to `jm new` to inline everything into
a single `just-makeit.toml`, as older projects (and the "combined schema" tab
below) do.

=== "File tree"

    ```
    my_project/
    ├── just-makeit.toml
    ├── objects/
    │   └── engine.toml
    ├── CMakeLists.txt
    ├── Makefile
    ├── pyproject.toml
    ├── bootstrap.toml
    ├── Doxyfile
    ├── zensical.toml
    ├── .gitignore
    ├── README.md
    ├── docs/
    │   ├── index.md
    │   └── api.md
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
    │       ├── bench_engine_core.c
    │       └── jm_bench.h
    └── src/
        └── my_project/
            ├── __init__.py
            ├── engine.pyi
            ├── tests/
            │   └── test_engine.py
            └── benchmarks/
                └── bench_engine.py
    ```

=== "just-makeit.toml (fragment layout)"

    ```toml
    include = ["objects/*.toml", "modules/*.toml"]

    [project]
    name    = "my_project"
    version = "0.1.0"
    build   = "cmake"
    perf    = "false"
    pytest  = "false"
    pytest_benchmark = "false"
    schema    = "7"
    jm_version = "0.29.0"
    ```

=== "objects/engine.toml"

    ```toml
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
    ```

=== "combined schema (--no-fragments)"

    Everything above, inlined into one `just-makeit.toml` — this is what
    `jm new --no-fragments` produces, and what every fragment ultimately
    means regardless of which file it lives in:

    ```toml
    [project]
    name             = "my_project"
    version          = "0.1.0"
    build            = "cmake"
    perf             = "false"
    pytest           = "false"
    pytest_benchmark = "false"

    [engine]
    arg_type    = "float _Complex"
    return_type = "float _Complex"
    mutable     = "false"
    no_state    = "false"
    no_step     = "false"

    [[engine.state]]
    name    = "gain"
    type    = "double"
    default = "1.0"

    [[engine.init_params]]
    name    = "order"
    type    = "int"
    default = "4"

    [[engine.array_args]]
    name = "coeffs"
    type = "float32"

    [[engine.methods]]
    name        = "normalize"
    return_type = "void"
    params      = [{name = "scale", type = "double"}]

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

______________________________________________________________________

## Module dependencies & external libraries

`jm apply` regenerates each module's `CMakeLists.txt`, so dependency wiring must
be **declared in the manifest** — a hand-edited link line is clobbered on the
next apply. Four keys cover every case; none of them require post-apply patching,
and `jm status --check` stays clean with no allowlist.

| You need…                                                        | Key                                       | Scope          | Effect                                                                                                                                                                                            |
| ---------------------------------------------------------------- | ----------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| link an external/system library                                  | `extra_link_libs`                         | `[module.<m>]` | appended verbatim to `target_link_libraries`; **generator expressions allowed**                                                                                                                   |
| link a *sibling* module's core                                   | `extra_link_libs` (name the `<obj>_core`) | `[module.<m>]` | names the target on the link line                                                                                                                                                                 |
| call into another object's API (link **and** include its header) | `depends_on`                              | `[<object>]`   | links `<dep>_core` **and** injects `#include "<dep>/<dep>_core.h"` (gh-170)                                                                                                                       |
| add a hand-written **C support dir** other modules link          | `c_deps`                                  | `[project]`    | emits `add_subdirectory(native/src/<dir>)`; that dir's `CMakeLists.txt` is hand-owned (never regenerated)                                                                                         |
| add a hand-written **Python extension module**                   | `no_generate`                             | `[module.<m>]` | emits the `add_subdirectory`, leaves the module's `_ext.c` / `.pyi` / CMake alone                                                                                                                 |
| keep a module's functions in **one TU**                          | `functions_in_core`                       | `[module.<m>]` | appends every function body to `<m>_core.c` (shared `static` helpers, one TU) instead of one `.c` per function; CMake lists only `<m>_core.c` (gh-247). Also `jm module <m> --functions-in-core`. |

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

Every TOML key the schema accepts maps to a CLI flag. This is a
standing design bar: no feature should require a TOML edit before it
can be used.

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
| `c_style`          | `jm new --c-style clang-format`           | ✅ (0.36.0)  | Reformat generated C — see below.      |
| `c_format_command` | (manifest only)                           | ✅ (0.43.3)  | Which formatter binary — see below.    |

### Generated-C house style — `c_style` and `c_format_command`

jm emits its own canonical 4-space C. A project with a different committed
style otherwise sees permanent drift: jm regenerates the `*_ext.c` binding in
4-space, the project's formatter rewrites it to house style, and
`jm status --check` calls it stale forever.

```toml
[project]
c_style = "clang-format"
c_format_command = ["uvx", "clang-format==22.1.8"]
```

`c_style` decides **whether** to format; `c_format_command` decides **which
binary does it**, and the second is what makes the result reproducible. Left
unset it defaults to `["clang-format"]` — a bare `PATH` lookup, which is fine
on one machine and wrong across two: clang-format 21 and 22 format the same
input differently, so a project whose developers and CI resolve different
versions gets a drift gate that flips red on a tree nobody touched. Point it
at whatever already pins the version (a `uvx` version specifier, a pre-commit
mirror, an absolute path) and the committed bytes stop depending on the
machine.

!!! note "Turning it on in an existing project needs one `jm apply`"

    There is no `jm config` key for this — you add the lines above to
    `just-makeit.toml` by hand. Formatting happens after a command that emits
    C, so declaring it changes nothing on its own: the committed `*_ext.c` is
    still 4-space while the tree `jm status` compares it against is now
    house-styled, and the next `jm status --check` reports it stale and exits
    1 on a tree you did not touch.

    `jm apply` reconciles it, once, permanently. Nothing is lost — the binding
    is generated glue, not your code. Do it in the same commit as the manifest
    change so no one else meets the red gate.

!!! warning "The command must not depend on the working directory"

    jm formats its **temp scaffold** — the tree `jm status` compares against
    — from outside your project. A command that resolves a different binary
    depending on where it runs therefore formats the two compared sides with
    two different formatters, and no number of `jm apply` runs clears the
    resulting drift.

    `["uv", "run", "--group", "dev", "clang-format"]` is exactly this trap.
    Outside a project `uv` prints `warning: --group dev has no effect when used outside of a project` and falls back to whatever is on `PATH`.

    Prefer `uvx clang-format==<version>` or an absolute path. `jm status`
    checks for this and names it when there is drift to explain.

It is an **argv list, never a shell string** — splitting a string would have to
guess about quoting, and the first thing that goes here is a path that may
contain spaces. jm appends `-i --style=file --fallback-style=LLVM` and the file
list, so the committed `.clang-format` still decides the layout.

**A project that formats its C gets a `.clang-format`** (gh-960).
`--style=file` with no file falls back to LLVM silently, so declaring the
formatter and shipping no style file was a project asking for a layout nobody
had chosen. `jm apply` now writes jm's house style file when it is absent, and
`jm status` reports one that is behind jm's current render as `OUTDATED` — a
file it could never compare before, because the tree it compares against was
seeded from the project's own copy. It is create-only like the rest: yours to
edit, and `[project] status_allow` says so once.

Scope: only the wholesale-regenerated `*_ext.c` glue is reformatted. `*_core.c`
and the splice-patched `native/inc/**` headers are left to the project's own
formatter — reformatting those breaks `jm apply` convergence.

A missing binary is a soft failure: one warning, and the command still
succeeds with generated C in jm's default style. When `jm status` reports
drift on a `c_style` project it also prints the formatter's version, so
"stale in CI, clean locally" names its own cause.

### Authored `@code` examples — the 79-column budget

An `@code` block in a sacred header becomes the `Examples` section of the
generated docstring. jm strips the `*` comment decoration and re-indents the
line to sit inside the docstring, so **the line gets shorter than it looks**:

| where the docstring lands    | stub indent | your `@code` line may be |
| ---------------------------- | ----------- | ------------------------ |
| a method or property         | 8           | **71** columns           |
| a class docstring (`create`) | 4           | **75** columns           |
| a module-level function      | 4           | **75** columns           |

A line wrapped to the header's own 79 columns is 74 columns of content once
`*` is counted — which fits the header and *overflows the stub by 3*. That
is why `jm apply` reports the concrete figure per site rather than a rule:

```
native/inc/cvt/cvt_core.h: cvt_step(): @code line will be 82 columns in the
  stub; wrap at <= 71.
    >>> c.step(2.0)                    # beyond +1.0 -> saturates to int16 max
```

**jm never rewrites the line.** A `>>>` is executable, and the overflow is
usually a trailing comment whose column you aligned deliberately. Three ways
to bring one back under budget, none of which changes what the example does:

1. **Trim the comment text**, keeping the `#` column. Most overflows are a few
    words of comment.
1. **Continue the statement** across a `...` continuation line — a doctest is one logical
    statement, so this is behaviour-preserving.
1. **Move the note above the example.** A doctest block runs prose, then the
    `>>>` code, then its output, then a blank line, then prose again — and the
    prose wraps freely, so a long aside reads better there anyway.

`jm status` prints the outstanding count, so a project sweeping them has a
burn-down number.

### Generated Python style — `py_format_command`

The Python twin of `c_format_command`. jm emits its own layout for the
generated `.pyi` stubs; a project that wants them in its own pinned style
hands jm the command:

```toml
[project]
py_format_command = ["uv", "run", "--group", "dev", "ruff", "format"]
```

Unset, nothing runs and output is byte-identical to before. There is no
separate on/off key — declaring the command *is* the opt-in.

**Why jm runs it rather than your pre-commit hook.** A `.pyi` is drift-gated:
`jm status --check` regenerates and compares byte-for-byte. A formatter run
outside jm therefore *creates* drift — your hook formats the file, jm
regenerates it unformatted, and no number of `apply` runs converges. Once jm
runs the formatter itself, it runs it on both the real tree and the throwaway
scaffold `apply` compares against, so the two sides are formatted by the same
command and compare equal.

That symmetry is also why jm's own emission does not need to match your
formatter: the *formatted* output is the fixed point, because formatters are
idempotent.

**Scope: `.pyi` stubs only.** A package `__init__.py` is deliberately excluded
— `apply` *merges* those, so they carry hand-written Python alongside the
generated re-exports, and reformatting a hybrid file rewrites your code. The
same reasoning keeps `c_style` off `native/inc/**`.

As with `c_format_command`: an argv list, never a shell string; only `argv[0]`
is resolved on `PATH`, so `uv run …` works when the formatter itself is not on
`PATH`; and a missing binary is a soft failure — one warning, the command
still succeeds, and *neither* tree is formatted, so they still compare equal.

### `[<component>]` keys

| TOML key             | CLI flag                                          | Status       | Notes                                                                                   |
| -------------------- | ------------------------------------------------- | ------------ | --------------------------------------------------------------------------------------- |
| `arg_type`           | `jm object --arg-type T`                          | ✅           |                                                                                         |
| `return_type`        | `jm object --return-type T`                       | ✅           |                                                                                         |
| `mutable`            | `jm object --mutable`                             | ✅           |                                                                                         |
| `no_state`           | `jm object --no-state`                            | ✅           |                                                                                         |
| `no_step`            | `jm object --no-step`                             | ✅           |                                                                                         |
| `no_reset`           | `jm object --no-reset`                            | ✅           | Removes `reset()` entirely — binding, C function, `.pyi` entry, tests.                  |
| `class_name`         | `jm object --class-name NAME`                     | ✅           |                                                                                         |
| `depends_on`         | (inferred from `--module`)                        | ✅           | Set automatically when an object lives in a module.                                     |
| `extra_link_libs`    | (component scope: TOML only)                      | 🟡           | Per-module is `jm module --extra-link-libs`; per-component still TOML-only (rare case). |
| `extra_include_dirs` | `jm object --extra-include-dirs DIR` (repeatable) | ✅ (0.13.23) |                                                                                         |

### `[[<component>.state]]` entries

| TOML field                            | CLI flag                                             | Status |
| ------------------------------------- | ---------------------------------------------------- | ------ |
| `name`, `type`, `default`             | `jm object --state name:type[:default]` (repeatable) | ✅     |
| `name`, `type`, `opaque = true`       | (TOML only)                                          | 🟡     |
| `name`, `type`, `no_ctor = true`      | (TOML only)                                          | 🟡     |
| `name`, `type`, `controllable = true` | (TOML only)                                          | 🟡     |

The three rare modifiers (`opaque`, `no_ctor`, `controllable`) currently
require editing `just-makeit.toml` directly. CLI flags are pending
(syntax under discussion: `--state name:type:opaque`,
`--state name:type:no-ctor`, `--state name:type:controllable`). Until
those land, hand-editing the manifest is the workaround.

`controllable = true` turns a state field into an optional per-call
override on `step()` / `steps()` — see
[Arguments — Default / optional arguments](arguments.md#default-optional-arguments)
for the full semantics.

### `[[<component>.init_params]]` entries

| TOML field                                                             | CLI flag                                                                | Status                      |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------- | --------------------------- |
| `name`, `type`, `default`                                              | `jm object --init-param name:type[:default]` (repeatable)               | ✅                          |
| `optional = true`                                                      | `jm object --init-param 'name:type[]:optional'`                         | ✅ (syntax extension)       |
| `default_raw = "<C constant>"` (a default jm must not evaluate)        | (TOML only)                                                             | ✅ (gh-1099)                |
| `real_type`, `real_create_fn`, `create_fn`                             | (TOML only)                                                             | 🟡                          |
| `capsule = "<name>"`, `header = "path/hdr.h"`                          | `jm object --init-param 'name:type:capsule:<name>[:<header>]'`          | ✅ (0.47.0)                 |
| `required = false` on a capsule param (nullable handle)                | `jm object --init-param 'name:type:capsule:<name>[:<header>]:optional'` | ✅ (gh-805 §H)              |
| `derived = "<name>"` (name a 1-D array's length parameter)             | (TOML only)                                                             | ✅ (gh-900)                 |
| `derived = ["<n0>", "<n1>"]` (name a 2-D array's extents)              | (TOML only)                                                             | ✅ (gh-1097)                |
| `c_type = "<typedef>"` (declare an integer param's C type)             | (TOML only)                                                             | ✅ (gh-1096)                |
| `example_value = "<literal>"` (a value generated tests construct with) | (TOML only)                                                             | ✅ (gh-1105)                |
| compose with `[[state]]`                                               | `--init-param + --state` together                                       | ✅ (0.13.23) (gate dropped) |

#### `example_value` — constructing a required param in generated tests

jm seeds a generated smoke test and doctest with the type's **zero**. For a
constructor that *validates* — rejecting a zero rate, span or size — that is
the one value it refuses, so jm suppresses the construction entirely and the
whole generated class skips:

```
SKIPPED: required constructor parameter(s) capacity, slots have no default;
         seed valid arguments to enable this smoke test
```

Skipping is safer than emitting a construction that cannot work, and it is not
the end state: the project then ships a test suite that asserts nothing.
`example_value` gives jm something valid to build with:

```toml
[[allocator.init_params]]
name          = "capacity"
type          = "size_t"
required      = true
example_value = "1024"
```

It is **not a default.** The parameter stays required, the Python signature is
unchanged, and `Allocator()` is still a `TypeError` — which matters, because a
validating constructor is usually one you *want* to be mandatory. One
declaration feeds the generated pytest, the generated C smoke test and the
docstring examples, so both faces exercise the same construction.

**It takes effect when the object is created.** The generated test files are
create-only — jm writes them once and never rewrites them — so adding
`example_value` to an existing project changes nothing already on disk.

**One thing changes with it.** For an init-params constructor the generated
accessor test asserts the set/get **round-trip only**, not the value a field
holds after construction. jm generates the state-var constructor whole, so
there it knows the initial value and still asserts it; with init-params the
constructor is the author's, and any state it *derives* from its arguments
makes that assertion a guess. "Reset restores the declared defaults" is still
asserted by the reset test on both faces, where the code under test is jm's.

#### A `default` is a literal; a constant is `default_raw`

`default` is rendered **verbatim** into four places — the C local, both `.pyi`
writers, and the generated app's `argparse` flags — so it has to be a literal
of the type declared beside it. jm refuses anything else, naming the type and
the value:

```toml
[[det.init_params]]
name    = "mode"
type    = "int"
default = "hann"        # error: not a valid `int` literal
```

A C **constant** is a real and common case, and it has its own key.
`default_raw` means "this is C, not a literal": the text goes into the C
unchanged and the Python side gets `...`, which is the honest answer for a
value jm cannot evaluate.

```toml
[[det.init_params]]
name        = "taps"
type        = "int"
default_raw = "DP_MAX_TAPS"
```

```c
int taps = DP_MAX_TAPS;
```

```python
def __init__(self, taps: int = ...) -> None: ...
```

The refusal names `default_raw` in its message, so the remedy arrives with the
error. Before gh-1099 that key was read **only** for types carrying a parse
intermediate — it worked on a `size_t` and was silently dropped on an `int`,
which fell back to the type's zero.

`const char *` is exempt: its value *is* text. `bool` accepts `true`/`false`
(and `0`/`1`) and says so in its own words when it does not.

#### Naming what the constructor declares

jm derives the `create()` prototype from `init_params`, and two parts of it
used to have no spelling. Both keys below change **only the declaration jm
injects into the sacred `_core.h`** — the Python face, the parse block and the
call are untouched, which is what makes them safe.

They matter because `jm status --check`'s CTOR comparison (gh-1076) is not
suppressible. A C signature the manifest could not describe was reported
forever, and `jm apply` "resolved" it by rewriting the author's header down to
jm's rendering.

**`c_type` — an enum typedef in the prototype.** A `string_enum:`/`enum:`
init-param renders `int`, because jm's type vocabulary has no enum typedef.
When the C really takes one, say so:

```toml
[[detector.init_params]]
name    = "noise_mode"
type    = "string_enum:mean,median,min,max"
default = "mean"
c_type  = "det_noise_mode_t"
```

```c
detector_state_t *detector_create(det_noise_mode_t noise_mode);
```

```python
Detector(noise_mode="median")   # unchanged
```

The binding still parses the choice string, validates it to an index and
passes an `int`; C converts at the call. That interchangeability is also the
limit, so it is enforced: `c_type` is accepted **only** on a parameter jm
declares as an integer. Over a `double` it would be a silent ABI mismatch that
still compiles, and jm refuses it by name.

**`derived` — naming an array's extents.** By default a 1-D array init-param
appends a trailing `<name>_len`, and a 2-D one (`type = "T[][]"`) appends
`<name>_dim0`, `<name>_dim1`. A string moves the 1-D length *before* the data
pointer and names it; a list names a 2-D array's extents in place:

```toml
[[corr2d.init_params]]
name    = "ref"
type    = "float _Complex[][]"
derived = ["ny", "nx"]
```

```c
corr2d_state_t *corr2d_create(const float complex *ref,
                              size_t ny, size_t nx, size_t dwell);
```

The Python face still takes one 2-D array, the binding still requires
`ndim == 2`, and it still passes both dimensions — only the declared names
change. The list must name **every** extent; a shorter one is refused, because
it would drop an extent from the declaration while the binding kept passing
it.

#### A capsule-typed init-param: constructing from a foreign handle

The constructor counterpart of the method params above — the object is built
*around* a pointer another module published. `header` injects the `#include`
that declares the foreign type into the sacred `_core.h`, because the type
appears in the `create()` prototype.

A capsule init-param is **mandatory by default**: there is usually no object
to build around a handle that is not there. Declare it `optional` when `NULL`
is a value that *means* something:

```toml
[[capture.init_params]]
name    = "clock"
type    = "dp_sample_clock_t *"
capsule = "doppler.clk"
header  = "clk.h"
# required omitted -> nullable
```

```python
Capture(clock)        # borrows the handle
Capture(None)         # C receives NULL -- "no time base stated"
```

`required = true` (the default, and what the CLI writes without `:optional`)
rejects `None` up front with a `TypeError` naming what to pass, rather than
letting a `NULL` reach `create()` and surfacing the failure a layer away from
its cause. Either way a wrong object — an `int`, say — gets a `TypeError`
naming the capsule, not the `AttributeError` from the internal `._capsule`
lookup.

The stub annotates a nullable handle `object | None`, **without** a `= None`
default: the argument still has to be passed. Being *omittable* is a separate
axis, and a stub advertising a default the binding does not honour is the
gh-611 defect this project ships a checker for.

The generated `create()`'s Doxygen says `May be NULL (Python: None).` on a
nullable handle, so the contract is visible where the author writes the body
that has to honour it.

### `[[<component>.methods]]` entries

| TOML field                             | CLI flag                                                     | Status       |
| -------------------------------------- | ------------------------------------------------------------ | ------------ |
| `name`, `arg_type`, `return_type`      | `jm method <obj> <method> --arg-type T --return-type T`      | ✅           |
| `doc = "..."`                          | `jm method --doc "text"`                                     | ✅           |
| `fn = "SYMBOL"`                        | `jm method --fn SYMBOL`                                      | ✅ (0.49.0)  |
| `params = [{name, type}]`              | `jm method --param name:type` (repeatable)                   | ✅           |
| `varargs = true`                       | `jm method --varargs`                                        | ✅           |
| `extra_args = [{name, type}]`          | `jm method --extra-arg name:type` (alias for `params`)       | ✅ (0.14.2)  |
| `variable_output = true`               | `jm method --variable-output`                                | ✅           |
| `pass_capacity = true`                 | `jm method --pass-capacity`                                  | ✅ (0.14.4)  |
| `exact_max_out = true`                 | `jm method --exact-max-out`                                  | ✅ (0.55.0)  |
| `count_default = "EXPR"`               | `jm method --count-default EXPR`                             | ✅ (0.34.0)  |
| `nogil = true`                         | `jm method --nogil`                                          | ✅ (0.15.2)  |
| `max_out = N` (sibling stub)           | `jm method --max-out N`                                      | ✅ (0.13.23) |
| `multi_output = ["T", ...]`            | `jm method --multi-output T` (repeatable)                    | ✅           |
| `out_type = "T"`                       | `jm method --out-type T`                                     | ✅           |
| `out_divisor = N`                      | `jm method --out-divisor N`                                  | ✅           |
| `batch = true`                         | `jm method --batch`                                          | ✅           |
| `bench = false`                        | `jm method --no-bench`                                       | ✅           |
| `result_fields = [{name, type, doc?}]` | `jm method --result-field name:type[:doc]` (repeatable)      | ✅ (0.13.23) |
| `single = true`                        | `jm method --single`                                         | ✅ (0.19.6)  |
| `record_name = "..."`                  | `jm method --record-name NAME` (with `--single`)             | ✅ (0.19.8)  |
| `record_module = "..."`                | `jm method --record-module MOD` (with `--single`)            | ✅ (0.19.14) |
| `record_doc = "..."`                   | `jm method --record-doc "text"` (with `--single`)            | ✅ (0.41.0)  |
| `record_dtype = "STRUCT"`              | `jm method --record-dtype STRUCT` (with `--variable-output`) | ✅ (0.47.0)  |
| `max_results = N`                      | (TOML only; default 64)                                      | 🟡           |
| `none_on_empty = true`                 | (TOML only) return `None` rather than an empty result        | 🟡           |
| `status_return = true`                 | (TOML only) the `int` return carries status only             | 🟡           |
| `error_negative = true`                | `jm method --error-negative`                                 | ✅ (0.49.0)  |
| `error = "EXC"`                        | `jm method --error EXC`                                      | ✅ (0.49.0)  |
| `error_message = "..."`                | `jm method --error-message TEXT`                             | ✅ (0.49.0)  |
| `py_return_type = "..."`               | `jm method --py-return-type STR`                             | ✅           |
| `manual_stub = "..."`                  | (TOML only) hand-written `.pyi` signature, kept verbatim     | 🟡           |
| `codec = "..."`                        | (TOML only) variant codec applied to the result              | 🟡           |
| `sink_fn = "SYMBOL"`                   | (TOML only) C sink the method feeds instead of returning     | 🟡           |
| `impl = "..."` body                    | `jm method --impl file::funcname`                            | ✅           |
| `impl_file`, `replace`                 | `jm method --impl file::funcname` / `--replace old::new`     | ✅           |

### `[[<component>.properties]]` entries

| TOML field                                      | CLI flag                                                               | Status      |
| ----------------------------------------------- | ---------------------------------------------------------------------- | ----------- |
| `name`, `type`                                  | `jm property <obj> <prop> --type T`                                    | ✅          |
| `writable = true`                               | `jm property --writable`                                               | ✅          |
| `field = true`                                  | `jm property --field`                                                  | ✅          |
| `buf_field`, `len_field`, `valid_field`, `expr` | `jm property --buf-field` / `--len-field` / `--valid-field` / `--expr` | ✅ (0.30.2) |

### `[[<component>.views]]` entries

| TOML field                          | CLI flag                                                | Status    |
| ----------------------------------- | ------------------------------------------------------- | --------- |
| `class_name`, `create_fn`           | `jm view <obj> <Class> --module <mod> --create-fn <fn>` | ✅ (0.31) |
| `doc = "..."`                       | `jm view --doc "text"`                                  | ✅ (0.31) |
| `init_params = [{name, type, ...}]` | `jm view --init-param name:type[:default]` (repeatable) | ✅ (0.31) |
| `exclude_properties = ["..."]`      | `jm view --exclude-property name` (repeatable)          | ✅ (0.31) |
| `exclude_methods = ["..."]`         | `jm view --exclude-method name` (repeatable)            | ✅ (0.31) |
| `properties = [{...}]`              | `jm property <obj> <prop> --view <Class>`               | ✅ (0.32) |
| `methods = [{...}]`                 | `jm method <obj> <meth> --view <Class>`                 | ✅ (0.32) |
| `warnings = [{...}]`                | `jm warning <obj> --view <Class>`                       | ✅ (0.33) |

### `[<component>]` lifecycle impl bodies

| TOML field                 | CLI flag                                   | Status       |
| -------------------------- | ------------------------------------------ | ------------ |
| `impl = "..."` (step body) | `jm object --impl file::funcname`          | ✅           |
| `impl_file = "path::N:M"`  | `jm object --impl file::N:M` (line range)  | ✅ (0.14)    |
| `create_impl = "..."`      | `jm object --impl create::file::funcname`  | ✅ (0.13.23) |
| `reset_impl = "..."`       | `jm object --impl reset::file::funcname`   | ✅ (0.13.23) |
| `destroy_impl = "..."`     | `jm object --impl destroy::file::funcname` | ✅ (0.13.23) |
| `init_post_parse = "..."`  | (TOML only)                                | 🟡           |

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
- **🟡 CLI flag pending**: 11 keys — rare modifiers (`opaque`, `no_ctor`, `controllable`, `init_post_parse`, `default_raw`/`real_type` init-param details, `no_generate` module, `max_results` / `max_results_param`). These are foot-guns to close: TOML is the persistence layer, not the user interface. Each will get a CLI flag in Phase 3.

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

| Key           | Type                     | Default            | Set by          |
| ------------- | ------------------------ | ------------------ | --------------- |
| `arg_type`    | string                   | `"float _Complex"` | `--arg-type`    |
| `return_type` | string                   | same as `arg_type` | `--return-type` |
| `mutable`     | `"true"` or `"false"`    | `"false"`          | `--mutable`     |
| `no_state`    | `"true"` or `"false"`    | `"false"`          | `--no-state`    |
| `no_step`     | `"true"` or `"false"`    | `"false"`          | `--no-step`     |
| `no_reset`    | `"true"` (only when set) | _(absent)_         | `--no-reset`    |

### `[[<object>.state]]`

One entry per `--state` declaration.

| Key       | Type   | Notes                                              |
| --------- | ------ | -------------------------------------------------- |
| `name`    | string | ASCII letters/digits/underscores, no leading digit |
| `type`    | string | C type; append `[N]` for fixed arrays              |
| `default` | string | C initialiser expression                           |

### `[[<object>.array_args]]`

Fixed-size array constructor arguments added with `--array-arg`.

| Key    | Type   | Notes                                                                                                                                                         |
| ------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name` | string | Argument name                                                                                                                                                 |
| `type` | string | Stored as NumPy dtype name (`float32`, `float64`, `complex64`, …); C types (`float`, `double`, `float _Complex`, …) are also accepted on input and normalised |

### `[[group]]` and `[[<object>.init_groups]]`

A **field group** is a repeat declared once and instantiated under a prefix
(gh-999). jm's type vocabulary has no struct in either direction, so a C
descriptor built from N copies of the same small field set had to be flattened
into one long name-prefixed constructor list — written out once per repeat,
with every `default`, `doc` and `enum` binding duplicated N times and free to
drift, since jm saw N unrelated params.

```toml
[[group]]
name = "wfm_seq"

[[group.fields]]
name    = "kind"
type    = "int"
enum    = "wfm_seq_kind"
default = "literal"
doc     = "Which sequence family this leg uses."

[[group.fields]]
name = "len"
type = "size_t"
```

```toml
[[frame.init_groups]]
group  = "wfm_seq"
prefix = "preamble"      # -> preamble_kind, preamble_len

[[frame.init_groups]]
group  = "wfm_seq"
prefix = "sync"          # -> sync_kind, sync_len
```

| Key      | Table                   | Notes                                                                                                                                                                                                                                                                                                                                                               |
| -------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`   | `[[group]]`             | The group's name, referenced by `init_groups.group`.                                                                                                                                                                                                                                                                                                                |
| `fields` | `[[group]]`             | `[[group.fields]]` entries. Every key an `init_params` entry accepts is accepted here — `type`, `default`, `doc`, `required`, `c_type`, `derived`, the array and capsule forms. (`enum` is **not** one of them: it is a method-param, property and function-param key. A constructor parameter spells its enum in `type`, as `enum:<name>` or `string_enum:a,b,c`.) |
| `group`  | `[[<obj>.init_groups]]` | Which group to instantiate.                                                                                                                                                                                                                                                                                                                                         |
| `prefix` | `[[<obj>.init_groups]]` | Prepended as `<prefix>_<field>`. Omit it to use the bare field names.                                                                                                                                                                                                                                                                                               |

**This is not jm learning structs**, and the expansion is *exactly* the
hand-written list. The C prototype, the kwlist, the `.pyi` and the docstrings
are byte-identical to writing the params out yourself — which is the point: it
declares the repeat, it does not add a type.

**`[[group]]` is a top-level SSOT table**, like `[[enum]]`. A group is
referenced by name from component tables in any fragment, so it lives in
`just-makeit.toml` rather than in one of them.

**The declaration is what round-trips.** The expansion happens when the
manifest is read and is folded back when it is written, so `jm apply`,
`jm property` and every other mutating command leave the two `init_groups` rows
in place and never write the expanded params out beside them.

Groups instantiate **after** the object's explicit `init_params`, which is the
order the manifest reads in — so a hand-written param and a grouped one coexist
predictably:

```toml
[[frame.init_params]]
name = "crc"
type = "int"
# -> crc, preamble_kind, preamble_len, sync_kind, sync_len
```

A row naming a group that does not exist is left alone rather than raised on:
`jm` reports the unrecognised declaration the way it reports any other typo,
instead of turning it into a traceback out of every command at once.

______________________________________________________________________

### `[[<object>.init_params]]`

Constructor-only parameters added with `--init-param` (no getter/setter, no
reset). Same `name` / `type` / `default` keys as `state`. Besides the scalar /
array types, two opaque pseudo-types are accepted (both required-positional, no
default): `type = "path"` (an `os.fspath` coerced to a borrowed `const char *`,
gh-515) and `type = "bytes"` (a read-only bytes-like coerced to a borrowed
`(const void *, size_t)` pair via `y#`, gh-565). The C constructor must copy
either borrow before returning.

An array init-param is a **required positional** by default. To make one
omittable, give it `default = "[]"` (gh-611):

```toml
[[frame.init_params]]
name    = "preamble"
type    = "uint8_t[]"
default = "[]"
```

Omitted — or passed `None` — it reaches `create()` as `NULL` with length `0`,
which is the convention C already uses for "no array". Any number of arrays
compose: they share **one** `create()` call with a `NULL`/`0` pair each, so a
constructor describing a composite of independently-absent parts is spelled
directly:

```python
Frame(sync=sync, crc="crc16")        # preamble and payload absent
```

`"[]"` is the only accepted default (an array has no other zero jm could
invent), and it is 1-D only.

**This is not `optional`.** That key is array *dispatch*: the array's presence
selects a different `create_fn` instead of `<component>_create`. Dispatch
picks one constructor, so it does not compose — jm refuses `optional` on more
than one array, and refuses it with no `create_fn`, pointing here in both
cases (gh-1004 / gh-1005). An init-param may also not be named
`<array>_len`, which is the length parameter jm derives for the array beside
it (gh-1002).

### `[[<object>.methods]]`

One entry per `just-makeit method` call.

| Key               | Type                    | Notes                                                             |
| ----------------- | ----------------------- | ----------------------------------------------------------------- |
| `name`            | string                  | Method name                                                       |
| `arg_type`        | string                  | Array-style input type                                            |
| `return_type`     | string                  | C return type                                                     |
| `params`          | array of `{name, type}` | Named scalar / array parameters                                   |
| `variable_output` | bool                    | `--variable-output`                                               |
| `pass_capacity`   | bool                    | `--pass-capacity` (5-arg `(…, out, max_out)` C form)              |
| `exact_max_out`   | bool                    | `--exact-max-out`: `max_out` bounds any call, so allocate exactly |
| `count_default`   | string                  | C expression seeding `count` for a void-input method (gh-657)     |
| `nogil`           | bool                    | `--nogil` (release the GIL across the kernel; see below)          |
| `status_return`   | bool                    | `int` return is a status: `-> None`, ValueError on non-0 (gh-432) |
| `batch`           | bool                    | `--batch`                                                         |
| `multi_output`    | array of strings        | `--multi-output` types                                            |
| `out_type`        | string                  | `--out-type`                                                      |
| `out_divisor`     | int                     | `--out-divisor` (default `1`; omitted from TOML when `1`)         |

`nogil` wraps the pure-C kernel of a `variable_output` execute method in
`Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS` (numpy accessors hoisted out
first), so a thread-per-shard worker — one object + output buffer per thread —
scales across cores instead of serialising on the GIL. Opt-in: it is sound
only when the object is not shared across threads concurrently (one object per
stream).

#### Capsule-typed params (gh-432)

A param may carry `capsule = "<name>"` (and optionally
`header = "path/hdr.h"`): its C type is a **foreign pointer** that crosses
the Python boundary as a named PyCapsule. Manifest-authored (no CLI flag
yet):

```toml
[[agc.methods]]
name          = "set_telemetry"
arg_type      = "void"
return_type   = "int"
status_return = true
params = [
  { name = "tlm",    type = "dp_tlm_t *",
    capsule = "doppler.telemetry.dp_tlm",
    header  = "telemetry/telemetry.h" },
  { name = "prefix", type = "const char *" },
  { name = "decim",  type = "uint32_t", default = "1" },
]
```

Generated binding semantics: `None` maps to `NULL` (the C-side detach
idiom); a PyCapsule is name-checked with `PyCapsule_GetPointer`; any other
object is unwrapped through its `_capsule` attribute first, so callers pass
the friendly wrapper (`obj.set_telemetry(tlm, "agc")`), not the capsule.
The `.pyi` annotates the param `object | None`. The `header` key injects
`#include "path/hdr.h"` into the object's `_core.h` alongside the gh-170
`depends_on` includes (skipped when the file doesn't exist under
`native/inc`). `status_return = true` binds the C `int` status as
`-> None`, raising `ValueError` on non-zero — the same contract as the
`serializable` `set_state` glue. Module functions accept capsule params
too (same parse builder); `status_return` is methods-only for now.

### `[[<object>.properties]]`

One entry per `just-makeit property` call.

| Key        | Type   | Notes                                                  |
| ---------- | ------ | ------------------------------------------------------ |
| `name`     | string | Property name                                          |
| `type`     | string | C type of the value                                    |
| `writable` | bool   | `--writable`                                           |
| `field`    | bool   | `--field` (adds struct member, auto-implements getter) |

### `[[<object>.views]]`

One entry per `just-makeit view` call — a **second Python class over the same
generated C core** (gh-504). The view shares `<object>_state_t` and the
object's `_core.c`; only its constructor and its Python surface differ. Views
are a module-object feature, so the object must belong to a `[module.<name>]`.

| Key                  | Type             | Notes                                                                                                                                                                        |
| -------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `class_name`         | string           | Python class name for the view. Required; unique across every class the module exposes.                                                                                      |
| `create_fn`          | string           | C constructor the view's `__init__` calls. Required; must differ from `<object>_create`. Scaffolded as a stub in the shared `_core.c`.                                       |
| `doc`                | string           | Docstring for the view class.                                                                                                                                                |
| `init_params`        | array            | The view's own constructor params, same shape as `[[<object>.init_params]]`. Omit to inherit the parent's constructor shape.                                                 |
| `exclude_properties` | array of strings | Parent property names the view omits from its Python surface.                                                                                                                |
| `exclude_methods`    | array of strings | Parent method names the view omits. Only the view's Python wrapper and `PyMethodDef` entry are dropped; the shared C function stays.                                         |
| `properties`         | array            | The view's **own** properties, same shape as `[[<object>.properties]]`: a new name ADDS a property the parent lacks, a parent's name OVERRIDES it. Merged over the parent's. |
| `methods`            | array            | The view's own methods, same shape as `[[<object>.methods]]`: ADD a new method (scaffolds a shared C stub) or OVERRIDE a parent method's doc.                                |
| `warnings`           | array            | The view's own post-construction warnings, same shape as `[[<object>.warnings]]` (gh-509). A view carries no parent warnings, so this is its only source.                    |

```toml
[[acc.views]]
class_name = "SeededAcc"
create_fn = "acc_create_seeded"
exclude_methods = ["total"]

[[acc.views.init_params]]
name = "seed"
type = "double"
default = "0.0"

[[acc.views.properties]]
name = "runs"        # a property the parent does not have
type = "size_t"
doc = "reseed count"
field = true
```

The nested tables are written by `just-makeit property|method|warning <obj> --view <ClassName>`; see
[Extend commands → `just-makeit view`](commands/extend.md#just-makeit-view).

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

## Variant codecs (`[codec.<name>]`)

A **variant codec** (gh-554) maps a runtime *discriminant* value — a small tag,
e.g. a BLUE/SigMF keyword's `char` type code — to a C **element width**, so one
value can be encoded and decoded as any of a fixed set of C types chosen at call
time. The *same* declared table drives both the input pack (Python → bytes) and
the output decode (bytes → Python), so the two directions **cannot drift** —
zero hand-written binding on either side.

Declared once at the top level, keyed by name (like `[module.<name>]`):

```toml
[codec.blue_keyword]
discriminant = "char"        # C type of the tag that selects a branch
scalar_collapse = true       # decode: count == 1 -> a scalar, else a list
entries = [
  { code = "A", ctype = "char",    bytes = true },  # raw bytes -> str
  { code = "B", ctype = "int8_t"  },                # -> int
  { code = "I", ctype = "int16_t" },
  { code = "L", ctype = "int32_t" },
  { code = "X", ctype = "int64_t" },
  { code = "F", ctype = "float"   },                # -> float
  { code = "D", ctype = "double"  },
]
```

| Codec key         | Type   | Notes                                                             |
| ----------------- | ------ | ----------------------------------------------------------------- |
| `discriminant`    | string | C type of the tag (`char`, or an int-family `_CTYPE_META` scalar) |
| `scalar_collapse` | bool   | Decode a lone element as a scalar rather than a 1-element list    |
| `entries`         | array  | `{ code, ctype, bytes? }` — one branch per discriminant value     |

Each entry's numeric `ctype` must be a scalar in `_CTYPE_META`; the Python type
it crosses as is **derived** from the ctype (`int` / `float`), so an entry never
declares a redundant `py`. A `bytes = true` entry is packed raw and decoded as
`str`.

### Write — a codec method (`[[<obj>.methods]]`)

A method with `codec` + `sink_fn` packs a variant argument into a host-order
buffer and calls the sink. Among its `params`, one carries `role = "discriminant"` (the tag), one `role = "variant"` (the value jm packs); the rest
are fixed passthroughs.

```toml
[[wfm_writer.methods]]
name = "add_keyword"
codec = "blue_keyword"
sink_fn = "wfm_writer_add_keyword"   # int (state, <fixed...>, <disc>, const void *, size_t)
params = [
  { name = "tag",   type = "const char *" },        # fixed passthrough
  { name = "type",  role = "discriminant" },        # the char code
  { name = "value", role = "variant" },             # jm packs per codec
]
```

jm generates the whole binding: parse, the per-code pack (a Python scalar **or**
any sequence → the coded C width), the `sink_fn` call, and a *precise* `.pyi`
union (`str | int | float | Sequence[int] | Sequence[float]`). jm does **not**
declare `sink_fn` — that stays your pure-C contract.

### Read — a codec container property (`[[<obj>.properties]]`)

A container property with `codec` decodes each entry back to Python. It reuses
the container cursor (`count_fn` / `key_fn` for a `dict`) and adds an `entry_fn`
that returns a pointer to one entry struct, whose fields the codec decodes.

```toml
[[wfm_reader.properties]]
name = "keywords"
codec = "blue_keyword"
count_fn = "wfm_reader_num_keywords"
key_fn = "wfm_reader_keyword_tag"
entry_fn = "wfm_reader_keyword"
entry_type = "wfm_keyword_t"   # see the default-derivation note below
type_field = "type"            # struct fields the decode reads
count_field = "count"
value_field = "value"
```

| Property key                                 | Default                    | Notes                                           |
| -------------------------------------------- | -------------------------- | ----------------------------------------------- |
| `codec`                                      | —                          | The `[codec.<name>]` to decode with             |
| `entry_fn`                                   | `<obj>_<prop>_entry`       | Returns `const <entry_type> *(state, i)`        |
| `entry_type`                                 | `<obj>_<prop>_t`           | The entry struct type — **often needs setting** |
| `type_field` / `count_field` / `value_field` | `type` / `count` / `value` | The discriminant / length / payload members     |
| `scalar_collapse`                            | codec's value              | Per-property override                           |
| `header`                                     | —                          | `#include` the `.pyi`/ext needs for the struct  |

jm generates the decode helper (bytes → `str`; a numeric branch decodes `count`
elements to `int`/`float`, collapsing to a scalar when `count == 1` if
`scalar_collapse`) and the `dict[str, str | int | float | list[int] | list[float]]` `.pyi`. As on the write side, jm declares **neither** `entry_fn`
**nor** the entry struct — both are your pure-C contract.

> **`entry_type` usually needs setting.** It defaults to `<obj>_<prop>_t`
> (property `keywords` on `wfm_reader` → `wfm_reader_keywords_t`), but a shared,
> element-named struct (`wfm_keyword_t`) will not match that guess and the decode
> helper won't compile. Set `entry_type` explicitly whenever the struct isn't
> named after the property.

### Error surfaces

Codec errors are intentionally generic: a non-zero `sink_fn` return raises
`ValueError: <method> failed`; an unknown discriminant raises `ValueError: unsupported code '<c>'`; a multi-character discriminant string is a
`PyArg`-level `TypeError`. If you need a domain-specific message, wrap the call
in Python.

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
