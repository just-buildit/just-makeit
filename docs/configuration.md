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

| Category                                                          | Stored in TOML                                    |
| ----------------------------------------------------------------- | ------------------------------------------------- |
| Project name and version                                          | Yes                                               |
| Build system (`--build-system`)                                   | Yes                                               |
| Performance annotations (`--perf`)                                | Yes                                               |
| Test runner (`--pytest`, `--pytest-benchmark`)                    | Yes                                               |
| Objects and their state variables                                 | Yes                                               |
| `arg-type`, `return-type`, `--mutable`, `--no-state`, `--no-step` | Yes                                               |
| Constructor parameters (`--init-param`)                           | Yes                                               |
| Extra methods, properties, module-level functions                 | Yes                                               |
| Module subpackage structure                                       | Yes                                               |
| `--impl` / `--replace` lifted code                                | No — spliced into the source once, never recorded |

A lifted `--impl` body is spliced into the sacred source once and is **not**
written to the manifest. To make a body re-stampable by `jm apply` /
`jm regenerate`, declare it there yourself: `impl` or `impl_file`,
`create_impl`, `reset_impl`, `destroy_impl` (each with a `_file` form), and
`replace` — see [lifecycle impl bodies](#component-lifecycle-impl-bodies).
Edits you make afterwards to the sacred `_core.c` are yours and live only in
source.

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
    ├── CMakePresets.json
    ├── Makefile
    ├── pyproject.toml
    ├── bootstrap.toml
    ├── Doxyfile
    ├── zensical.toml
    ├── .clang-tidy
    ├── .gitattributes
    ├── .gitignore
    ├── README.md
    ├── benchmarks/
    │   └── history/
    │       └── .gitkeep
    ├── docs/
    │   ├── index.md
    │   └── api.md
    ├── cmake/
    │   ├── my_project-config.cmake.in
    │   └── my_project.pc.in
    ├── native/
    │   ├── inc/
    │   │   └── my_project/
    │   │       ├── my_project.h
    │   │       ├── clib_common.h
    │   │       ├── pyex_common.h
    │   │       └── engine/
    │   │           └── engine_core.h
    │   ├── src/
    │   │   ├── my_project_lib.c
    │   │   └── engine/
    │   │       ├── engine_core.c
    │   │       ├── engine_ext.c
    │   │       └── CMakeLists.txt
    │   ├── tests/
    │   │   ├── jm_test.h
    │   │   ├── test_engine_core.c
    │   │   └── test_engine_symbols.c
    │   └── benchmarks/
    │       ├── bench_engine_core.c
    │       └── jm_bench.h
    └── src/
        └── my_project/
            ├── __init__.py
            ├── engine.pyi
            ├── tests/
            │   ├── __init__.py
            │   └── test_engine.py
            └── benchmarks/
                ├── __init__.py
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
    schema     = "8"
    jm_version = "x.y.z"   # stamped by jm new / jm apply
    c_prefix   = "my_project"
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
    schema           = "8"
    jm_version       = "x.y.z"   # stamped by jm new / jm apply
    c_prefix         = "my_project"

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
next apply. Five keys cover every case; none of them require post-apply
patching, and `jm status --check` stays clean with no allowlist.

| You need…                                                        | Key                                       | Scope          | Effect                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------- | ----------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| link an external/system library                                  | `extra_link_libs`                         | `[module.<m>]` | appended verbatim to `target_link_libraries`; **generator expressions allowed**                                                                                                                                                               |
| link a *sibling* module's core                                   | `extra_link_libs` (name the `<obj>_core`) | `[module.<m>]` | names the target on the link line                                                                                                                                                                                                             |
| call into another object's API (link **and** include its header) | `depends_on`                              | `[<object>]`   | links `<dep>_core` **and** injects `#include "<pkg>/<dep>/<dep>_core.h"` (gh-170)                                                                                                                                                             |
| add a hand-written **C support dir** other modules link          | `c_deps`                                  | `[project]`    | emits `add_subdirectory(native/src/<dir>)`; that dir's `CMakeLists.txt` is hand-owned (never regenerated)                                                                                                                                     |
| add a hand-written **Python extension module**                   | `no_generate`                             | `[module.<m>]` | emits the `add_subdirectory`, leaves the module's `_ext.c` / `.pyi` / CMake alone                                                                                                                                                             |
| keep a module's functions in **one TU**                          | `functions_in_core`                       | `[module.<m>]` | appends every function body to `<m>_core.c` (shared `static` helpers, one TU) instead of one `.c` per function; CMake lists only `<m>_core.c` (gh-247). Also `jm module <m> --functions-in-core` (at creation; afterwards edit the manifest). |

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

Set it from the CLI with `jm module <m> --extra-link-libs TARGET` (repeatable;
at creation, afterwards edit the manifest).

For a package, prefer its **imported target** (`doppler::doppler-static`,
`PkgConfig::FFTW3F`) over a path or a `${VAR}` holding one. A target carries
its include dirs and flags, and jm passes those on to consumers of your
installed library. A path carries neither, and it would put this machine's
layout into the installed package. See [When your library depends on another
package](c-library.md#when-your-library-depends-on-another-package).

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

An entry may also be a table. `{ name = "source", link = true }` additionally
puts `source_core` on the consuming extension's own link line, which a
dependency whose functions you call needs (gh-225).
`{ name = "x", test_only = true }` links `x_core` into the component's C test
and benchmark only, so a sibling the test merely round-trips through never
ships in the artifact (gh-537):

```toml
[waveform_engine]
depends_on = [
  { name = "source", link = true },
  { name = "writer", test_only = true },
]
```

### `c_deps` — hand-written C support directories

For a pure-C directory (object libraries, vendored code) that has **no** Python
binding but that modules link against:

```toml
[project]
c_deps = ["io", "vendor_dsp"]   # add_subdirectory(native/src/io), …
```

Each listed dir owns its `CMakeLists.txt` (define `add_library(<name>_core …)`,
tests, etc.); modules then link it via `extra_link_libs = ["io_core", …]`. CLI:
`jm new --c-dep DIR` (repeatable; at creation, and it writes the
`add_subdirectory` itself). Afterwards edit the manifest and run `jm apply`.

### `no_generate` — hand-written extension modules

For a module whose binding is written by hand (e.g. a free-function API over an
opaque capsule):

```toml
[module.io]
no_generate = "true"
no_generate_reason = "free-function API over an opaque capsule; a handle module (gh-306) would replace it"
```

`jm apply` emits the `add_subdirectory(native/src/io)` and otherwise leaves the
module untouched — `io_ext.c`, `io.pyi`, and its `CMakeLists.txt` are yours.
So are the files of every object it lists: a command that rebuilds an object
from the manifest (`jm regenerate`, `jm add --state`, `jm remove state`)
refuses one here, since `apply` would write none of it back (gh-2087). Change
its C by hand.

`no_generate_reason` is required: `jm status --check` fails on an opt-out that
does not say why (gh-1313). Say which kind it is — a shape jm cannot express
(name the issue that would change that), or a module nobody has migrated yet.
Those want opposite treatment and look identical without a reason. A reason
left on a module that no longer opts out fails the check too; delete it.
Pair with [`reexports`](#modulename-keys) on a sibling module to fold its symbols
into a generated package `__init__.py`.

### Worked example: three interdependent modules + a C support dir

```toml
[project]
c_deps = ["io"]                    # hand-written native/src/io (io_core, …)

[module.source]
objects = ["source", "lfsr"]
extra_link_libs = ["doppler::doppler-static", "io_core", "m"]

[module.wfm]
objects = ["waveform_engine"]
extra_link_libs = ["source_core", "lfsr_core", "m"]
# …or, if waveform_engine calls source/lfsr APIs, drop those two libs and use:
#   [waveform_engine]
#   depends_on = ["source", "lfsr"]
```

This is the wiring doppler uses — `c_deps`, `extra_link_libs`, and
`depends_on` with `link = true` — fully declarative, idempotent across
`jm apply`.

______________________________________________________________________

## Complete CLI ↔ TOML mapping

Most TOML keys map to a CLI flag; rows whose CLI cell says *(manifest
only)* or *(TOML only)* have none yet. The standing design bar is that
no feature should require a TOML edit before it can be used.

Status legend: ✅ shipped · 🟡 shipped, CLI flag pending (TOML works
today).

> CLI and TOML are both first-class authoring paths. The CLI is the
> recommended way (presets, validators, errors); TOML editing is a
> fully supported alternative for power users or for knobs the CLI
> hasn't yet exposed. The 🟡 rows below are TOML-only today. That is
> an open CLI-parity gap, not a design choice; the goal is parity.

### `[project]` keys

| TOML key              | CLI flag                                  | Status       | Notes                                                                                                                                                                                                                                                                                                                                                    |
| --------------------- | ----------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                | `jm new <NAME>`                           | ✅           | Required positional.                                                                                                                                                                                                                                                                                                                                     |
| `version`             | `jm config version X`                     | ✅           | Written by `jm new`; omit it to defer to `pyproject.toml` (see [below](#omitting-version-defers-to-pyprojecttoml)).                                                                                                                                                                                                                                      |
| `build`               | `jm new --build-system cmake\|make`       | ✅           |                                                                                                                                                                                                                                                                                                                                                          |
| `perf`                | `jm new --perf` / `jm perf`               | ✅           | Retrofit available via `jm perf`.                                                                                                                                                                                                                                                                                                                        |
| `pytest`              | `jm new --pytest`                         | ✅           |                                                                                                                                                                                                                                                                                                                                                          |
| `pytest_benchmark`    | `jm new --pytest-benchmark`               | ✅           |                                                                                                                                                                                                                                                                                                                                                          |
| `find_packages`       | `jm new --find-package NAME` (repeatable) | ✅ (0.13.23) | CMake `find_package(NAME REQUIRED)`; an entry may be `{ name, pkg_config }`, or `{ name, libs_private, cflags }` for a dependency with no `.pc`, for the installed `.pc` — see [c-library](c-library.md#when-your-library-depends-on-another-package).                                                                                                   |
| `pkg_modules`         | `jm new --pkg-module NAME` (repeatable)   | ✅ (0.13.23) | pkg-config via `pkg_check_modules`; an entry may carry a version bound, `"zlib >= 1.2"`.                                                                                                                                                                                                                                                                 |
| `public_link_libs`    | (manifest only)                           | ✅           | Link flags a consumer of the installed headers needs (`["-lpthread"]`): on this project's executables and extensions, both combined libraries' PUBLIC link, the exported targets and the `.pc`'s `Libs:` — see [c-library](c-library.md#flags-your-headers-need-of-every-consumer).                                                                      |
| `public_defines`      | (manifest only)                           | ✅           | Definitions the installed headers need (`["_GNU_SOURCE"]`, no `-D`): on this project's own compile, both combined libraries' PUBLIC definitions, the exported targets and the `.pc`'s `Cflags:`.                                                                                                                                                         |
| `c_deps`              | `jm new --c-dep DIR` (repeatable)         | ✅ (0.13.23) | Vendored C subdir (no Python wrapper).                                                                                                                                                                                                                                                                                                                   |
| `c_prefix`            | `jm new --c-prefix P` / `--no-c-prefix`   | ✅           | **Default for a new project: its package name.** Namespaces every C symbol jm derives (`fir_create` becomes `P_fir_create`, `FIR_CORE_H` becomes `P_FIR_CORE_H`), so two installed packages may share a component name; never an author-named symbol or macro, the Python names, or file names — see [c-library](c-library.md#two-packages-one-program). |
| `schema`              | (managed by `jm upgrade`)                 | ✅           | Migrated; no user-facing flag.                                                                                                                                                                                                                                                                                                                           |
| `jm_version`          | (managed)                                 | ✅           | Stamped by `jm new`; `jm apply` raises it to the running jm and never lowers it (gh-183).                                                                                                                                                                                                                                                                |
| `c_style`             | `jm new --c-style clang-format`           | ✅ (0.36.0)  | Reformat generated C — see below.                                                                                                                                                                                                                                                                                                                        |
| `bench.timeout`       | `jm bench --timeout S` (one run)          | ✅           | `[project.bench] timeout`: seconds one benchmark run may take before `jm bench` skips it, keeps the rest and exits 1. Default 600; `0` = no limit (gh-1687).                                                                                                                                                                                             |
| `bench.block_sizes`   | (manifest only)                           | ✅           | `[project.bench] block_sizes`: the block sizes the generated Python benchmarks run. Default `[1024, 65536]`; the C benchmark is unaffected.                                                                                                                                                                                                              |
| `c_format_command`    | (manifest only)                           | ✅ (0.43.3)  | Which formatter binary — see below.                                                                                                                                                                                                                                                                                                                      |
| `py_format_command`   | (manifest only)                           | ✅           | Formatter for the generated `.pyi` stubs — see [below](#generated-python-style-py_format_command).                                                                                                                                                                                                                                                       |
| `status_allow`        | `jm status --allow PATH` (one run)        | ✅           | Paths / globs `jm status` reports as `ALLOWED` instead of counting as drift — see [`status`](commands/build.md#just-makeit-status).                                                                                                                                                                                                                      |
| `strict_examples`     | `jm status --strict-examples` (one run)   | ✅ (gh-760)  | An authored `@code` line too wide for its stub fails `jm status --check` instead of being counted — see [below](#authored-code-examples-the-79-column-budget).                                                                                                                                                                                           |
| `libraries`           | (manifest only)                           | ✅ (gh-1600) | `[project.libraries.<name>]`: one more installed library built from the OBJECT libraries it names — see [c-library](c-library.md#more-than-one-library).                                                                                                                                                                                                 |
| `platforms`           | (none)                                    | retired      | No longer does anything (gh-1368): a `"windows"` entry prints a notice, and `jm apply` drops the old MinGW blocks. Delete the key. The module-level [`platforms`](#a-module-built-on-some-platforms-only) is a different, live key.                                                                                                                      |
| `include` (top level) | `jm new` (fragment layout)                | ✅           | Globs naming the fragment files merged into the manifest: `["objects/*.toml", "modules/*.toml"]`. Absent under `jm new --no-fragments`.                                                                                                                                                                                                                  |

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
    `just-makeit.toml` by hand. Formatting happens after a command that
    changes the project, so declaring it changes nothing on its own: the
    committed `*_ext.c` is still 4-space while the tree `jm status` compares
    it against is now house-styled, and the next `jm status --check` reports
    it stale and exits 1 on a tree you did not touch.

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

Scope: only the CPython binding jm writes is reformatted — each `*_ext.c` and
the per-object `<module>_ext_<obj>.c` fragments (gh-917), never a hand-written
`*_ext_extra.c`. `*_core.c` and the splice-patched `native/inc/**` headers are
left to the project's own formatter — reformatting those breaks `jm apply`
convergence.

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

A line wrapped to the header's own 79 columns is 76 columns of content once
the three-column `*` decoration is stripped — which fits the header and
*overflows a method's stub by 5*. That is why `jm apply` reports the
concrete figure per site rather than a rule:

```
native/inc/my_project/cvt/cvt_core.h: my_project_cvt_step(): @code line will be 82 columns in the
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

### Docstrings — `doc` is rendered as you write it

A `doc` on an object, a view, a method, a property, a module, a module
function, a state field, an init param, or a method or function param renders
**verbatim**, identically in the `.pyi` stub and in `help()` at runtime. So
does every `doc` a `handle`, `capsule` or `composer` module accepts: the
module itself, a method, a getter field, a create arg, a factory and its
params, a property, a composer source or segment field, a computed property,
an extra method, a serializer and a setting.

A handle getter's own `doc` documents the property only when the getter has
exactly one field. On a struct getter backing several properties it is
refused, because it documents none of them: put the text on each field's
`doc` instead.

```toml
doc = """
This is an example.
It has two lines.
"""
```

becomes exactly

```text
This is an example.
It has two lines.
```

jm does not reflow, join, flatten or truncate it, and it does not re-wrap a
long line: the layout is yours. The one normalisation is Python's own
docstring rule, [`inspect.cleandoc`](https://docs.python.org/3/library/inspect.html#inspect.cleandoc):
blank lines around the text go, and indentation common to every line but the
first is removed. So the text may start on the `"""` line, and an indented
TOML table's continuation lines lose their indent:

```toml
    doc = """What happens in this case?
        This is an example.
        It has two lines.
        """
```

renders the same three lines. A `doc` on a param or state field becomes that
parameter's entry under `Parameters`, and outranks the header's `@param`.

jm still generates the numpy sections — `Parameters`, `Returns`,
`Examples` — itself, so write prose in `doc`, not those headings: a `doc`
carrying one gets a second copy after it, which `jm status` reports as
`DOC`. For a full docstring with its own sections and doctests, write it as
Doxygen above the declaration in the component's `_core.h`.

The exception is a `doc` that is the member's whole docstring, with nothing
generated beside it. Write the whole numpy docstring, sections and doctests
included, in the `doc` of an
[`extra_methods`](#componentextra_methods-entries) row, which has no core
declaration to put Doxygen on. The same goes for an object's or a view's
property, a capsule's methods and properties, a handle's getters, and a
composer's serializers and computed properties. `status` does not report
these (gh-2059).

### Generated Python style — `py_format_command`

The Python twin of `c_format_command`. jm emits its own layout for the
generated `.pyi` stubs; a project that wants them in its own pinned style
hands jm the command:

```toml
[project]
py_format_command = ["uvx", "ruff==<version>", "format"]
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

**Scope: `.pyi` stubs and the generated `test_*_invariants.py` tests
(gh-1432).** Both are jm's alone, rewritten whole. A package `__init__.py` is
deliberately excluded — `apply` *merges* those, so they carry hand-written
Python alongside the generated re-exports, and reformatting a hybrid file
rewrites your code. The same reasoning keeps `c_style` off `native/inc/**`.

As with `c_format_command`: an argv list, never a shell string; only `argv[0]`
is resolved on `PATH`, so `uvx …` works when the formatter itself is not on
`PATH`; and a missing binary is a soft failure — one warning, the command
still succeeds, and *neither* tree is formatted, so they still compare equal.

### `[<component>]` keys

| TOML key                                 | CLI flag                                          | Status       | Notes                                                                                                                                                                                          |
| ---------------------------------------- | ------------------------------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `arg_type`                               | `jm object --arg-type T`                          | ✅           |                                                                                                                                                                                                |
| `return_type`                            | `jm object --return-type T`                       | ✅           |                                                                                                                                                                                                |
| `mutable`                                | `jm object --mutable`                             | ✅           |                                                                                                                                                                                                |
| `no_state`                               | `jm object --no-state`                            | ✅           |                                                                                                                                                                                                |
| `no_step`                                | `jm object --no-step`                             | ✅           |                                                                                                                                                                                                |
| `no_reset`                               | `jm object --no-reset`                            | ✅           | Removes `reset()` entirely — binding, C function, `.pyi` entry, tests.                                                                                                                         |
| `class_name`                             | `jm object --class-name NAME`                     | ✅           |                                                                                                                                                                                                |
| `doc`                                    | (manifest only)                                   | ✅ (gh-1172) | The class docstring, rendered [as you write it](#docstrings-doc-is-rendered-as-you-write-it).                                                                                                  |
| `opaque_state`                           | `jm object --opaque-state`                        | ✅ (gh-588)  | Forward-declares the state struct; its definition stays in `_core.c`.                                                                                                                          |
| `header_only`                            | `jm object --header-only`                         | ✅ (gh-1311) | The core is all `static inline` in `_core.h`; no `_core.c`, and the CMake core library is INTERFACE.                                                                                           |
| `serializable`                           | `jm object --serializable`                        | ✅           | `state_bytes()` / `get_state()` / `set_state()` over a C triplet in `_core.c`.                                                                                                                 |
| `streamable`                             | `jm object --streamable`                          | ✅           | `stream(block, *, count, on_block)` and `__iter__`.                                                                                                                                            |
| `stream_block_default`                   | `jm object --stream-block N`                      | ✅           | Default block for `__iter__` / `stream()`; implies `streamable`.                                                                                                                               |
| `async_stream`                           | `jm object --async-stream`                        | ✅           | Adds `__aiter__` / `__anext__`; implies `streamable`.                                                                                                                                          |
| `step_delegates_to_steps`                | `jm object --step-delegates-to-steps`             | ✅ (gh-208)  | `step()` is a thin delegator to `steps()`.                                                                                                                                                     |
| `create_fn`                              | `jm object --create-fn FN`                        | ✅           | The C constructor `tp_init` calls instead of `<pkg>_<comp>_create()`; same parameters.                                                                                                         |
| `array_args`                             | `jm object --array-arg name:dtype` (repeatable)   | ✅           | See [`[[<object>.array_args]]`](#objectarray_args).                                                                                                                                            |
| `create_error`, `create_error_message`   | `jm error <obj> --category EXC --message TEXT`    | ✅ (gh-482)  | Translate a `create()` failure into a Python exception.                                                                                                                                        |
| `warnings`                               | `jm warning <obj> --condition F --message TEXT`   | ✅ (gh-481)  | Post-construction `PyErr_WarnEx`.                                                                                                                                                              |
| `records`                                | `jm record <obj> <Struct> --field name:type`      | ✅ (gh-1405) | A C struct and its columns, named once for both directions.                                                                                                                                    |
| `views`                                  | `jm view <obj> <Class> --module M --create-fn FN` | ✅ (gh-504)  | See [`[[<component>.views]]`](#componentviews-entries).                                                                                                                                        |
| `fragment`                               | `jm adopt <obj>`                                  | ✅ (gh-1448) | Who owns a module object's binding fragment — see [below](#who-owns-a-modules-binding-fragment).                                                                                               |
| `process_global`                         | (manifest only)                                   | ✅ (gh-1117) | One copy of a core's state across every extension module that links it — see [Shared process state](shared-state.md).                                                                          |
| `core_macro`, `core_args`, `core_header` | (manifest only)                                   | ✅ (gh-1310) | A template family's one C macro — see [`core_macro`](#one-c-family-core_macro).                                                                                                                |
| `init_groups`                            | (manifest only)                                   | ✅ (gh-999)  | Instantiate a `[[group]]` of init-params under a prefix — see [below](#group-and-objectinit_groups).                                                                                           |
| `destroy`                                | (manifest only)                                   | ✅ (gh-541)  | `[<obj>.destroy]`: the destructor's name, aliases, return and error — see [declarative scaffolding](declarative-scaffolding.md).                                                               |
| `extra_methods`                          | (manifest only)                                   | ✅ (gh-1997) | Rows for methods whose CPython function you write in the object's `_extra.c` — see [below](#componentextra_methods-entries).                                                                   |
| `module`                                 | (manifest only)                                   | ✅           | In a fragment passed to `jm apply <fragment>`, the module the object joins.                                                                                                                    |
| `impl`, `create_impl`, …                 | (manifest only)                                   | ✅           | Lifecycle bodies `jm apply` re-stamps — see [below](#component-lifecycle-impl-bodies).                                                                                                         |
| `depends_on`                             | (manifest only)                                   | ✅           | Objects this one calls into: names, or `{ name, link = true }` / `{ name, test_only = true }` tables — see [`depends_on`](#depends_on-link-and-include-a-dependency). Never set automatically. |
| `extra_link_libs`                        | (component scope: TOML only)                      | 🟡           | Per-module is `jm module --extra-link-libs`; per-component still TOML-only (rare case).                                                                                                        |
| `extra_include_dirs`                     | `jm object --extra-include-dirs DIR` (repeatable) | ✅ (0.13.23) |                                                                                                                                                                                                |

### `[[<component>.state]]` entries

| TOML field                            | CLI flag                                                | Status       |
| ------------------------------------- | ------------------------------------------------------- | ------------ |
| `name`, `type`, `default`             | `jm object --state name:type[:default]` (repeatable)    | ✅           |
| `name`, `type`, `opaque = true`       | (TOML only)                                             | 🟡           |
| `name`, `type`, `no_ctor = true`      | (TOML only)                                             | 🟡           |
| `name`, `type`, `controllable = true` | (TOML only)                                             | 🟡           |
| `str_hint = "<text>"` (array field)   | (TOML only; see [Array parameters](commands/extend.md)) | ✅ (gh-1761) |
| `doc = "<text>"`                      | (TOML only)                                             | ✅ (gh-1493) |

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

| TOML field                                                             | CLI flag                                                                     | Status                      |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------- |
| `name`, `type`, `default`                                              | `jm object --init-param name:type[:default]` (repeatable)                    | ✅                          |
| `optional = true` (+ `create_fn`)                                      | `jm object --init-param 'name:type[]:optional[:create_fn]'`                  | ✅ (syntax extension)       |
| `required = true` (a scalar with no default)                           | `jm object --init-param 'name:type:required'`                                | ✅ (gh-266)                 |
| `doc = "<text>"`                                                       | (TOML only)                                                                  | ✅                          |
| `type = "enum:<name>"` / `type = "string_enum:a,b"`                    | `jm object --init-param 'name:enum:<name>[:default]'` (or `string_enum:a,b`) | ✅ (gh-1489)                |
| `default_raw = "<C constant>"` (a default jm must not evaluate)        | (TOML only)                                                                  | ✅ (gh-1099)                |
| `real_type`, `real_create_fn`                                          | (TOML only)                                                                  | 🟡                          |
| `capsule = "<name>"`, `header = "path/hdr.h"`                          | `jm object --init-param 'name:type:capsule:<name>[:<header>]'`               | ✅ (0.47.0)                 |
| `object = "<comp>[.<Class>]"` (derives `type`/`capsule`/`header`)      | `jm object --init-param 'name:object:<comp>[.<Class>][:optional]'`           | ✅ (gh-1224)                |
| `required = false` on a capsule param (nullable handle)                | `jm object --init-param 'name:type:capsule:<name>[:<header>]:optional'`      | ✅ (gh-805 §H)              |
| `derived = "<name>"` (name a 1-D array's length parameter)             | `jm object --init-param 'name:type[]:derived:<c-param>'`                     | ✅ (gh-900)                 |
| `derived = ["<n0>", "<n1>"]` (name a 2-D array's extents)              | (TOML only)                                                                  | ✅ (gh-1097)                |
| `c_type = "<typedef>"` (declare an integer param's C type)             | (TOML only)                                                                  | ✅ (gh-1096)                |
| `example_value = "<literal>"` (a value generated tests construct with) | (TOML only)                                                                  | ✅ (gh-1105)                |
| `str_hint = "<text>"` (an array refuses a `str`, with this appended)   | (TOML only; see [Array parameters](commands/extend.md))                      | ✅ (gh-1756)                |
| `rank = N` (refuse an array of any other rank)                         | (TOML only; see [Array parameters](commands/extend.md))                      | ✅ (gh-2004)                |
| `elements_per_sample = N` (`create()` gets the length in samples)      | (TOML only; see [Array parameters](commands/extend.md))                      | ✅ (gh-2004)                |
| compose with `[[state]]`                                               | `--init-param + --state` together                                            | ✅ (0.13.23) (gate dropped) |

#### `example_value` — constructing a required param in generated tests

jm seeds a generated smoke test and doctest with the type's **zero**. For a
constructor that *validates* — rejecting a zero rate, span or size — that is
the one value it refuses.

Whether yours does is a fact about C, and jm does not read your `_core.c`. So
the generated tests **ask** rather than assume (gh-1109): each makes the
zero-seeded call once, and skips only if it is rejected, carrying your
constructor's own message.

```
SKIPPED: required constructor parameter(s) capacity, slots have no default;
         seed valid arguments to enable this smoke test:
         my_project_allocator_create returned NULL
```

The decision is made when the test **runs**, not when it is written, which is
what makes it safe: adding validation to a `create()` whose tests were
generated before you did turns them into skips on the next run, never into
failures. Until you add it, jm's own scaffolded `create()` ignores the
parameter, so the suite runs and asserts.

A skip is still a suite that asserts nothing, and that is what `example_value`
is for — it gives jm something valid to build with:

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

It is also the only answer for the **docstring examples**, which have no
runtime probe available to them: a doctest is executable prose with nowhere to
put a fallback, so the `Examples` block stays suppressed until a value jm can
show is declared. The same is true of an init-param with no seed at all — a
`path`, a `bytes` blob, a `capsule` handle — where there is no call to attempt
and the generated tests skip unconditionally.

**Adding it later reaches the Python faces, not the C smoke.** The scaffolded
`tests/test_<comp>.py` and `benchmarks/bench_<comp>.py` are jm's while they
start `# jm:generated`, so the next `jm apply` rewrites them to construct with
the value; the `.pyi` and the runtime docstrings, which `apply` always
regenerates, pick it up as well. The C smoke test,
`native/tests/test_<comp>_core.c`, is yours from the moment it is written:
`apply` never rewrites it, so it keeps its zero-seeded call until you edit the
`create()` arguments yourself. Which files `apply` rewrites, and how one
becomes yours: [Who owns each file](workflows/edit-lifecycle.md#who-owns-each-file).

**One thing changes with it.** For an init-params constructor the generated
accessor test asserts the set/get **round-trip only**, not the value a field
holds after construction. jm generates the state-var constructor whole, so
there it knows the initial value and still asserts it; with init-params the
constructor is the author's, and any state it *derives* from its arguments
makes that assertion a guess. The reset test is the same guess one step later
-- `reset()` returns to the post-create state, which that constructor defines
-- so on both faces it only calls `reset()` (gh-1882), as it does for a
`reset_impl`.

#### A `default` is a literal; a constant is `default_raw`

`default` is rendered into four places — verbatim into the C local, and as
the same value spelled in Python into both `.pyi` writers and the generated
app's `argparse` flags — so it has to be a literal of the type declared beside
it, and one Python can spell ([Defaults](types.md#defaults)). jm refuses
anything else, naming the type and the value:

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

A **state** field's `default` is different: it may be a header constant
(`--state threshold:int:LVL_INFO`), because every state field is optional and
the C faces compile against it. Python cannot spell it, so the stub signature
says `threshold: int = ...`, generated construction calls leave it out (the
binding's default, the constant, applies), the reset test compares against
the value read back from a fresh object, and the docstring names the constant
(`threshold : int, default LVL_INFO`). A `bool` field is the same: any
default other than `true`/`false`/`0`/`1` (`--state on:bool:FLAG_ON`) is
treated as a constant.

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
my_project_detector_state_t *my_project_detector_create(det_noise_mode_t noise_mode);
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
my_project_corr2d_state_t *my_project_corr2d_create(const float _Complex *ref,
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

From the CLI a capsule init-param is **mandatory** unless you add `:optional`:
there is usually no object to build around a handle that is not there. In a
hand-written table it is **nullable unless `required = true`**. Leave
`required` out when `NULL` is a value that *means* something:

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

`required = true` (what the CLI writes without `:optional`) rejects `None` up
front with a `TypeError` naming what to pass, rather than letting a `NULL`
reach `create()` and surfacing the failure a layer away from its cause.
Either way a wrong object — an `int`, say — gets a `TypeError` naming the
capsule, not the `AttributeError` from the internal `._capsule` lookup.

The stub annotates a nullable handle `object | None`, **without** a `= None`
default: the argument still has to be passed. Being *omittable* is a separate
axis, and a stub advertising a default the binding does not honour is the
gh-611 defect this project ships a checker for.

The generated `create()`'s Doxygen says `May be NULL (Python: None).` on a
nullable handle, so the contract is visible where the author writes the body
that has to honour it.

#### `object`: naming another generated class instead of its capsule

When the pointer comes from **another jm-generated object**, say so directly
rather than restating the capsule string:

```toml
[[wfm_compose.init_params]]
name     = "frame"
object   = "frame.FrameDesc"   # <component>[.<ClassName>]
required = false
```

```sh
jm object seg --init-param 'frame:object:frame.FrameDesc'
jm object seg --init-param 'frame:object:frame.FrameDesc:optional'
```

`object` **resolves to the capsule form above** — it derives `type`
(`<pkg>_<comp>_state_t *`), `capsule` (read from the referenced component's own
capsule property) and `header`, so all three are omitted. The generated C is
byte-for-byte the capsule path; what changes is the declaration and the stub:

|                  | `capsule = "..."`    | `object = "frame.FrameDesc"`        |
| ---------------- | -------------------- | ----------------------------------- |
| the capsule name | written at both ends | read from the producer              |
| a typo           | fails at runtime     | refused at generation               |
| the `.pyi`       | `frame: object`      | `frame: FrameDesc`, with its import |

The name is **read, not derived**, because the producer already owns that
string; deriving a second one from the component id would be a second opinion
about it, and the two would drift the first time either changed. Declaring
`object` and `capsule` together is refused for the same reason.

A **view** is a legal target (`frame.FrameDesc` above is one), and resolves to
the same capsule — it is the same C core. Naming a component that publishes no
capsule is refused with the `jm property` line that would fix it.

A **`kind = "handle"` module is a legal target too** (gh-1227), and it is the
better-founded half of the feature. gh-794 exists precisely so a handle can
hand its pointer to another module, and a handle *declares* what that pointer
is:

```toml
[module.wfm_writer]
kind        = "handle"
backing     = "wfm_writer"
handle_type = "wfm_writer_t"        # the capsule lends a `wfm_writer_t *`
capsule     = "proj.wfm.writer"     # gh-794's module-level key
type_name   = "Writer"

[[seg.init_params]]
name   = "w"
object = "wfm_writer"               # -> wfm_writer_t *, from the declaration
```

Every slot is **read**: `handle_type` gives the C type (the generated struct
stores exactly `<handle_type> *h`, and the capsule lends it), `capsule` the
name, `header` the include, `type_name` the class. A handle generates one
class, so `wfm_writer.Writer` is accepted and any other suffix is refused. A
`capsule` or `composer` module is still not a target.

The `type` is **read when the producer states it and inferred otherwise**.
Undeclared, it is `<pkg>_<comp>_state_t *`, which is right for a producer
publishing the default `self->handle`. A capsule property may instead carry an
`expr` reaching a member — and then the pointer is something no consumer can
name, so the producer says so with `capsule_type` (gh-1235):

```toml
[[frame.properties]]
name         = "_capsule"
type         = "capsule"
capsule      = "p.frame.desc"
expr         = "&self->handle->d"        # publishes a member...
capsule_type = "const wfm_frame_desc_t *"  # ...and this is what it IS
```

```sh
jm property frame _capsule --type capsule --capsule p.frame.desc \
    --expr '&self->handle->d' --capsule-type 'const wfm_frame_desc_t *'
```

It is deliberately **not** `ctype`, which on a property is a legacy synonym for
`type` — and a capsule property's `type` is already the word `capsule`, so
reusing it would be one key answering two questions.

An `expr`-publishing producer that declares no `capsule_type` is **refused**
rather than resolved to a type the capsule does not carry, naming both fixes:
declare it on the producer, or write the type out at the consumer the gh-790
way.

!!! note "This is sugar, not a new mechanism"

    Two generated objects could always be wired constructor-to-constructor:
    the binding accepts the producing object itself, not just its capsule, and
    has since gh-790. What was missing was a way to *say so* — and the
    ergonomics mattered, because nothing checked that the two ends named the
    same string.

    It resolves *to* a capsule deliberately. Type-checking the object and
    reading its `handle` straight out of the struct would need the producer's
    object layout inside a consumer `.so` compiled separately, and possibly by
    a different jm version — the ABI hazard the capsule exists to avoid.

### `[[<component>.methods]]` entries

| TOML field                             | CLI flag                                                                                            | Status       |
| -------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------ |
| `name`, `arg_type`, `return_type`      | `jm method <obj> <method> --arg-type T --return-type T`                                             | ✅           |
| `doc = "..."`                          | `jm method --doc "text"`                                                                            | ✅           |
| `fn = "SYMBOL"`                        | `jm method --fn SYMBOL`                                                                             | ✅ (0.49.0)  |
| `params = [{name, type}]`              | `jm method --param name:type` (repeatable)                                                          | ✅           |
| `params … {out = true}` (or `mutable`) | `jm method --out-param name:T[]` (writable, repeatable)                                             | ✅           |
| `params … {str_hint = "..."}`          | (TOML only) an array refuses a `str`, with this appended                                            | ✅ (gh-1756) |
| `params … {rank = N}`                  | (TOML only) an array of any other rank is refused                                                   | ✅ (gh-805)  |
| `params … {elements_per_sample = N}`   | (TOML only) the kernel's counts are in samples of N elements                                        | ✅ (gh-1996) |
| `varargs = true`                       | `jm method --varargs`                                                                               | ✅           |
| `extra_args = [{name, type}]`          | `jm method --extra-arg name:type` (alias for `params`)                                              | ✅ (0.14.2)  |
| `variable_output = true`               | `jm method --variable-output`                                                                       | ✅           |
| `pass_capacity = true`                 | `jm method --pass-capacity`                                                                         | ✅ (0.14.4)  |
| `exact_max_out = true`                 | `jm method --exact-max-out`                                                                         | ✅ (0.55.0)  |
| `count_default = "EXPR"`               | `jm method --count-default EXPR`                                                                    | ✅ (0.34.0)  |
| `count_name = "NAME"`                  | `jm method --count-name NAME`                                                                       | ✅ (gh-1074) |
| `out_cols = "EXPR"`                    | `jm method --out-cols EXPR`                                                                         | ✅ (gh-2115) |
| `nogil = true`                         | `jm method --nogil`                                                                                 | ✅ (0.15.2)  |
| `max_out = N` (sibling stub)           | `jm method --max-out N`                                                                             | ✅ (0.13.23) |
| `multi_output = ["T", ...]`            | `jm method --multi-output T` (repeatable)                                                           | ✅           |
| `out_type = "T"`                       | `jm method --out-type T`                                                                            | ✅           |
| `out_divisor = N`                      | `jm method --out-divisor N`                                                                         | ✅           |
| `batch = true`                         | `jm method --batch`                                                                                 | ✅           |
| `bench = false`                        | `jm method --no-bench`                                                                              | ✅           |
| `result_fields = [{name, type, doc?}]` | `jm method --result-field name:type[:doc]` (repeatable)                                             | ✅ (0.13.23) |
| `single = true`                        | `jm method --single`                                                                                | ✅ (0.19.6)  |
| `record_name = "..."`                  | `jm method --record-name NAME` (with `--single`)                                                    | ✅ (0.19.8)  |
| `record_module = "..."`                | `jm method --record-module MOD` (with `--single`)                                                   | ✅ (0.19.14) |
| `record_doc = "..."`                   | `jm method --record-doc "text"` (with `--single`)                                                   | ✅ (0.41.0)  |
| `record_dtype = "STRUCT"`              | `jm method --record-dtype STRUCT` (with `--variable-output`)                                        | ✅ (0.47.0)  |
| `max_results = N`                      | (TOML only; default 64)                                                                             | 🟡           |
| `none_on_empty = true`                 | `jm method --none-on-empty` (an empty result is `None`)                                             | ✅ (gh-1418) |
| `error_on_empty = true`                | `jm method --error-on-empty` (an empty result raises)                                               | ✅ (gh-1159) |
| `count_type = "int64_t"`               | `jm method --count-type T` (a signed count; with `error_negative`, `< 0` raises)                    | ✅ (gh-2012) |
| `error_sentinel = "SIZE_MAX"`          | `jm method --error-sentinel EXPR` (a `size_t` count's refusal value raises)                         | ✅ (gh-2012) |
| `status_return = true`                 | `jm method --status-return` (the `int` is status only)                                              | ✅ (gh-823)  |
| `borrow = true`                        | `jm method --borrow` (a zero-copy view of state memory)                                             | ✅ (gh-1312) |
| `borrow_count = "PARAM"`               | `jm method --borrow-count PARAM`                                                                    | ✅ (gh-1312) |
| `borrow_writeable = true`              | `jm method --borrow-writeable`                                                                      | ✅ (gh-1312) |
| `status_fn = "SYMBOL"`                 | `jm method --status-fn SYMBOL` (why a borrow returned NULL)                                         | ✅ (gh-1418) |
| `status_errors = [{...}]`              | `jm method --status-error STATUS:Exc[:msg]` (with `--status-fn`; repeatable)                        | ✅ (gh-1418) |
| `releases = ["..."]`                   | `jm method --releases NAME[,NAME…]`                                                                 | ✅ (gh-1426) |
| `release_count = "PARAM"`              | `jm method --release-count PARAM`                                                                   | ✅ (gh-1426) |
| `strict = true`                        | `jm method --strict` (refuse, never cast, an array input)                                           | ✅ (gh-1426) |
| `error_negative = true`                | `jm method --error-negative`                                                                        | ✅ (0.49.0)  |
| `error = "EXC"`                        | `jm method --error EXC`                                                                             | ✅ (0.49.0)  |
| `error_message = "..."`                | `jm method --error-message TEXT`                                                                    | ✅ (0.49.0)  |
| `py_return_type = "..."`               | `jm method --py-return-type STR`                                                                    | ✅           |
| `manual_stub = true`                   | `jm method --manual-stub` (binding hand-written in `_ext_<obj>_extra.c`; `.pyi` stub kept verbatim) | ✅ (gh-428)  |
| `codec = "..."`                        | (TOML only) variant codec applied to the result                                                     | 🟡           |
| `sink_fn = "SYMBOL"`                   | (TOML only) C sink the method feeds instead of returning                                            | 🟡           |
| `impl = "..."` body                    | (manifest only; `jm method --impl` lifts once and records nothing)                                  | ✅           |
| `impl_file`, `replace`                 | (manifest only; `--impl` / `--replace` record nothing)                                              | ✅           |

### `[[<component>.properties]]` entries

| TOML field                                                                      | CLI flag                                                                                         | Status      |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------- |
| `name`, `type`                                                                  | `jm property <obj> <prop> --type T`                                                              | ✅          |
| `writable = true`                                                               | `jm property --writable`                                                                         | ✅          |
| `field = true`                                                                  | `jm property --field`                                                                            | ✅          |
| `buf_field`, `len_field`, `valid_field`, `expr`                                 | `jm property --buf-field` / `--len-field` / `--valid-field` / `--expr`                           | ✅ (0.30.2) |
| `doc = "..."`                                                                   | `jm property --doc "text"`                                                                       | ✅          |
| `enum = "<name>"`                                                               | `jm property --enum NAME` (decode through a top-level `[[enum]]`)                                | ✅          |
| `value_type`, `count_fn`, `key_fn`, `value_fn`                                  | `jm property --type dict\|list\|tuple --value-type T` / `--count-fn` / `--key-fn` / `--value-fn` | ✅          |
| `capsule = "<name>"`, `capsule_type = "T *"`                                    | `jm property --type capsule --capsule NAME` / `--capsule-type T`                                 | ✅          |
| `codec`, `entry_fn`, `entry_type`, `type_field` / `count_field` / `value_field` | (TOML only) see [Variant codecs](#variant-codecs-codecname)                                      | 🟡          |

### `[[<component>.extra_methods]]` entries

A method jm cannot express is written by hand, as a CPython function in the
object's `_extra.c` — `native/src/<comp>/<comp>_ext_extra.c` for a standalone
object, `native/src/<cname>/<cname>_ext_<comp>_extra.c` in a module — a file
jm includes and never modifies. A row here is what makes it reachable: jm
renders its `PyMethodDef` entry into the object's method table, a prototype
above the table, the `#include` of the file and the `.pyi` member. It is the
composer's [`extra_methods`](object-of-objects.md) key, for an ordinary object
(gh-1997).

```toml
[[fft.extra_methods]]
name    = "execute_ci16"                 # the Python method name
fn      = "FFTObj_execute_ci16"          # the C function you write
flags   = "METH_VARARGS"                 # default METH_NOARGS
args    = "iq: NDArray[np.int16]"        # raw Python, for the .pyi
returns = "NDArray[np.complex64]"        # raw Python; default None
doc     = "FFT of interleaved int16 I/Q."
```

| TOML field                                      | CLI flag    | Status       |
| ----------------------------------------------- | ----------- | ------------ |
| `name`, `fn`, `flags`, `args`, `returns`, `doc` | (TOML only) | ✅ (gh-1997) |

The `doc` is the method's whole docstring on both faces, the `.pyi` member
and the runtime `__doc__`, and jm generates nothing beside it. So it can be
a full numpy docstring with `Parameters`, `Returns` and an `Examples`
doctest (gh-2059). A `doc` that ends in an example gets a blank line before
the stub's closing quotes, so a text-mode `.pyi` doctest ends the example
there (gh-2176).

Write the function with exactly the signature its `flags` imply, `self`
included as a `PyObject *` (cast it to the object's struct inside):
`METH_NOARGS`, `METH_O` and `METH_VARARGS` take
`(PyObject *self, PyObject *arg)`, `METH_KEYWORDS` adds a `PyObject *kwds`,
and `METH_FASTCALL` rows get CPython's fastcall signature. jm declares it with
that signature above the table, so a different one does not compile. A
declared row always includes the file, so it can be written before or after
the `apply` that declares it; if it is missing, the build fails naming it.

In a module the prototypes go in the aggregator, before the object's
fragment, so a row works whether the fragment is sacred or `generated` — which
is what lets a hand-written method survive [`jm adopt`](#who-owns-a-modules-binding-fragment):
move the function into the `_extra.c`, declare its row, and the fragment's
table loses nothing on the flip. The core functions it calls stay
link-checked: the object's `native/tests/test_<obj>_symbols.c` is read from
the hook as well as the binding (gh-2175).

The rows follow the generated ones in the table, in the order declared. `apply`
refuses a row whose `name` a member jm generates already holds (`reset`, a
property, `__enter__`, …) or a `[[<component>.methods]]` entry declares, two
rows of one name, one `fn` given two different `flags`, an `fn` the object's
`_core.h` declares (a core function, not a CPython method), and an `fn` the
binding jm generates already declares — a wrapper such as `Solo_reset`, a
type's `Solo_dealloc` or `SoloType`, read off jm's own render of the
`_ext.c` (in a module, of every fragment it includes), since the row's
prototype would conflict with it (gh-2005). A
`manual_stub` method of the same name may stand beside the row: the row's stub
replaces its placeholder, and a stub you wrote by hand stays yours. A view
does not inherit the rows — each function is written against the parent's
struct. `jm script` names the rows in a NOTE, since no flag spells them.

### `[[<component>.views]]` entries

| TOML field                             | CLI flag                                                      | Status      |
| -------------------------------------- | ------------------------------------------------------------- | ----------- |
| `class_name`, `create_fn`              | `jm view <obj> <Class> --module <mod> --create-fn <fn>`       | ✅ (0.31)   |
| `doc = "..."`                          | `jm view --doc "text"`                                        | ✅ (0.31)   |
| `init_params = [{name, type, ...}]`    | `jm view --init-param name:type[:default]` (repeatable)       | ✅ (0.31)   |
| `exclude_properties = ["..."]`         | `jm view --exclude-property name` (repeatable)                | ✅ (0.31)   |
| `exclude_methods = ["..."]`            | `jm view --exclude-method name` (repeatable)                  | ✅ (0.31)   |
| `properties = [{...}]`                 | `jm property <obj> <prop> --view <Class>`                     | ✅ (0.32)   |
| `methods = [{...}]`                    | `jm method <obj> <meth> --view <Class>`                       | ✅ (0.32)   |
| `warnings = [{...}]`                   | `jm warning <obj> --view <Class>`                             | ✅ (0.33)   |
| `create_error`, `create_error_message` | `jm error <obj> --view <Class> --category EXC --message TEXT` | ✅ (gh-580) |

### `[<component>]` lifecycle impl bodies

| TOML field                                     | CLI flag        | Status       |
| ---------------------------------------------- | --------------- | ------------ |
| `impl = "..."` (step body)                     | (manifest only) | ✅           |
| `impl_file = "path::funcname"` / `"path::N:M"` | (manifest only) | ✅ (0.14)    |
| `create_impl = "..."` / `create_impl_file`     | (manifest only) | ✅ (0.13.23) |
| `reset_impl = "..."` / `reset_impl_file`       | (manifest only) | ✅ (0.13.23) |
| `destroy_impl = "..."` / `destroy_impl_file`   | (manifest only) | ✅ (0.13.23) |
| `replace = { "old" = "new" }`                  | (manifest only) | ✅           |
| `init_post_parse = "..."`                      | (TOML only)     | 🟡           |

`jm apply` and `jm regenerate` re-stamp these bodies from the manifest. Each
`_impl` and its `_impl_file` are mutually exclusive, and `replace` applies to
the `impl` / `impl_file` body only.

`jm object --impl` is a different thing: a one-shot lift at scaffold time that
splices a body into the generated source and records nothing in the manifest
(`--impl file::funcname`, `--impl create::file::funcname` and the other slot
prefixes, `--replace old::new`). Its `--impl file::N:M` form lifts source
lines `N..M` (inclusive, 1-based) instead of a named function body; it
composes with the slot prefixes (`create::file::N:M`) and out-of-bounds
ranges error cleanly.

### `[module.<name>]` keys

| TOML key                              | CLI flag                                          | Status       |
| ------------------------------------- | ------------------------------------------------- | ------------ |
| `objects` (list)                      | (auto-populated by `jm object --module <mod>`)    | ✅           |
| `extra_link_libs`                     | `jm module --extra-link-libs TARGET` (repeatable) | ✅ (0.13.23) |
| `extra_include_dirs`                  | `jm module --extra-include-dirs DIR` (repeatable) | ✅ (0.13.23) |
| `extra_types`                         | `jm module --extra-types NAME` (repeatable)       | ✅ (0.13.23) |
| `functions_in_core = "true"`          | `jm module --functions-in-core`                   | ✅ (gh-247)  |
| `doc = "..."`                         | `jm module --doc STR`                             | ✅           |
| `no_generate = "true"`                | (TOML only)                                       | 🟡           |
| `no_generate_reason = "..."`          | (TOML only)                                       | 🟡           |
| `functions`                           | (auto-populated by `jm function --module <mod>`)  | ✅           |
| `reexports = { sub = ["name", ...] }` | (TOML only)                                       | 🟡 (0.15.1)  |
| `platforms = ["linux", "macos"]`      | (TOML only)                                       | 🟡           |
| `package = "<dir>"`                   | `jm module --package DIR`                         | ✅ (gh-2064) |

`reexports` folds names from a *sibling* extension (typically a `no_generate`
module whose binding/`.pyi` are hand-written) into this module's generated
`__init__.py` — both the import block and `__all__` — so the re-export glue
regenerates from the manifest instead of being a hand-edit `jm apply` would
clobber. Output is single-line, matching the rest of the package.

`package` (gh-523) puts the module's Python in a sibling package's directory
instead of one named after the module — `package = "io"` lands a `reader`
module in `src/<pkg>/io/`, beside the classes already there. That package's `__init__.py`
re-exports the module's classes, so they import from `<pkg>.<package>`, and
everything jm writes that names one follows it (gh-2054): the `.pyi` and
runtime doctests, `test_<obj>.py` and the element contract beside it, an
`object` reference's `.pyi` import, and the `pep723` and `console` apps. The
extension keeps its own name inside it (`<pkg>/<package>/<module>.so`).
Declare it with `jm module <name> --package <dir>`: set in the manifest after
the module exists, the key leaves what `jm module` wrote in the module's own
directory behind (gh-2081). The value is a directory below `src/<pkg>/`: one
or more `/`-separated segments, each an ASCII identifier (`io`, `dsp/io`), or
`"."` for the package itself. Anything else (`../evil`, `a b`, `1x`,
`Other-Pkg`, or the dotted `a.b`) is refused before anything is written, on
the flag and when the manifest loads.

#### A module built on some platforms only

`platforms` (gh-1463) names the platforms a module's extension is built on,
from `linux`, `macos` and `windows`. Use it when the module sits on a core
that only exists on some platforms — a POSIX-only library whose CMake target
you define under `if(NOT WIN32)` yourself:

```toml
[module.stream_sink]
kind = "handle"
package = "wfm"
extra_link_libs = ["$<TARGET_OBJECTS:stream_core_obj>"]
platforms = ["linux", "macos"]
```

It applies to every module kind and changes two generated files:

- the module's `CMakeLists.txt` builds its extension under
    `if(BUILD_PYTHON AND (<platform test>))` instead of `if(BUILD_PYTHON)`, so
    elsewhere nothing references the missing target;
- wherever the module's names are imported into an `__init__.py` — its own,
    or another module's `reexports` — the import and the names' `__all__`
    entry move into a block that runs only on those platforms. Elsewhere the
    names are **absent**, and the rest of the package imports normally.

A platform jm does not know is refused when the manifest loads, rather than
quietly dropped from the build. Listing all three is the same as leaving the
key out. The key scopes the Python extension only: a plain module's C cores
still compile everywhere, and your own `<module>_extra.cmake` runs after the
guard, so guard anything platform-specific in it yourself.

### `[[module.<name>.functions]]` entries

| TOML field                                    | CLI flag                                                                          | Status       |
| --------------------------------------------- | --------------------------------------------------------------------------------- | ------------ |
| `name`, `return_type`, `doc`                  | `jm function <fn> --module <mod> --return-type T --doc STR`                       | ✅           |
| `params = [{name, type, out?}]`               | `jm function --param name:T` + `--out-param name:T[]`                             | ✅ (0.13.22) |
| `params … {mutable = true}`                   | (synonym for `out` — writable array param)                                        | ✅ (0.15.3)  |
| `params … {str_hint = "..."}`                 | (TOML only) an array refuses a `str`, with this appended                          | ✅ (gh-1756) |
| `params … {rank = N}`                         | (TOML only) an array of any other rank is refused                                 | ✅ (gh-805)  |
| `params … {elements_per_sample = N}`          | (TOML only) the C counts samples of N elements                                    | ✅ (gh-805)  |
| `inline = true`                               | `jm function --inline`                                                            | ✅           |
| `out_type = "T"`                              | `jm function --out-type T`                                                        | ✅ (0.13.23) |
| `out_type = "T[n]"`                           | `jm function --out-type 'T[n]'`                                                   | ✅ (gh-1888) |
| `variable_output = true`, `out_size = "EXPR"` | `jm function --variable-output --out-type T --out-size EXPR`                      | ✅ (gh-335)  |
| `out_type = "str"`                            | (TOML only)                                                                       | 🟡 (0.71.2)  |
| `check_return = true`                         | `jm function --check-return`                                                      | ✅ (gh-363)  |
| `why = true`                                  | `jm function --why` (with `--check-return`)                                       | ✅ (gh-1706) |
| `status_errors = [{...}]`                     | `jm function --status-error STATUS:Exc[:msg]` (with `--check-return`; repeatable) | ✅ (gh-1614) |
| `result_fields = [{name, type}]`              | `jm function --result-field name:type` (repeatable)                               | ✅ (0.13.23) |
| `max_results`                                 | (TOML only)                                                                       | 🟡           |
| `max_results_param`                           | (TOML only)                                                                       | 🟡           |
| `impl = "..."` body                           | (manifest only; `jm function --impl` lifts once and records nothing)              | ✅           |
| `impl_file`, `replace`                        | (manifest only; `--impl` / `--replace` record nothing)                            | ✅           |

#### A function that returns a string

`out_type = "str"` is the one `out_type` that is not an array of a C scalar
(gh-1180). On a `variable_output` function it makes jm allocate the buffer,
call C, and hand back a Python `str` — the caller allocates nothing:

```toml
[[module.cvt.functions]]
name           = "bin_to_hex"
return_type    = "size_t"      # the number of characters written
variable_output = true
out_type       = "str"
out_size       = "bits_len * 2"   # the capacity, as C over the args

[[module.cvt.functions.params]]
name = "bits"
type = "uint8_t[]"
```

```c
/* in project my_project: the C symbol carries the c_prefix */
size_t my_project_bin_to_hex(const uint8_t *bits, size_t bits_len, char *out);
```

```python
bin_to_hex(np.array([0x1a, 0xcf, 0xfc, 0x1d], np.uint8))   # '1acffc1d'
```

`str` names the **Python** shape; the C you implement takes a `char *`. The
integer return is required and jm refuses without one — a `void` function
cannot say how much it wrote, and hunting for a NUL the callee may never have
written is a read past the end waiting to happen. `char` is deliberately not a
supported scalar type: `char[]` in a param position is refused with a message
naming `uint8_t[]` for a byte buffer and this key for text.

### Counts

Read the column above rather than a number here: this section used to state
counts, and they had drifted to 11 🟡 and ~66 ✅ while the table said 16 and 91.

- **✅ on main**: every common path (Phase 2 stack shipped in 0.13.23)
- **🟡 CLI flag pending**: rare modifiers (`opaque`, `no_ctor`, `controllable`, `init_post_parse`, `real_type`/`real_create_fn` init-param details, `no_generate` module, `max_results` / `max_results_param`). These are foot-guns to close: TOML is the persistence layer, not the user interface.

Phase 2 acceptance bar — "every TOML field has a 'Reachable via CLI' column ✓" — is met for the common path. The remaining 🟡 rows are open CLI-parity gaps, not by-design exceptions.

______________________________________________________________________

## Schema reference

### `[project]`

| Key                | Type                  | Default                          | Set by                                             |
| ------------------ | --------------------- | -------------------------------- | -------------------------------------------------- |
| `name`             | string                | —                                | `just-makeit new <name>`                           |
| `version`          | string                | `pyproject.toml`, else `"0.1.0"` | `just-makeit new` / `just-makeit config version X` |
| `build`            | `"cmake"` or `"make"` | `"cmake"`                        | `--build-system make`                              |
| `perf`             | `"true"` or `"false"` | `"false"`                        | `--perf`                                           |
| `pytest`           | `"true"` or `"false"` | `"false"`                        | `--pytest`                                         |
| `pytest_benchmark` | `"true"` or `"false"` | `"false"`                        | `--pytest-benchmark`                               |
| `c_prefix`         | C identifier          | the package name (`jm new`)      | `--c-prefix P` / `--no-c-prefix`                   |
| `schema`           | string                | the current schema               | `jm new`; migrated by `jm upgrade`                 |
| `jm_version`       | string                | the running jm                   | `jm new`; raised by `jm apply`                     |

The full key list, with the CLI flag for each, is the
[Complete CLI ↔ TOML mapping](#complete-cli-toml-mapping) above.

#### Omitting `version` defers to `pyproject.toml`

`version` is the one `[project]` key you may delete. Leave it out and jm reads
`[project] version` from `pyproject.toml` instead of declaring a version of its
own:

```toml
# just-makeit.toml
[project]
name = "my_project"
# no version -- pyproject.toml is authoritative
```

The manifest then stops being a version carrier. That matters because a release
has to bump every file that holds a copy, and `just-makeit.toml` was one a human
had to remember; deleting it also makes the `VERSION` drift finding
*unrepresentable* for `pyproject.toml` rather than merely detected, since the
file jm reads the version **from** cannot disagree with it.

Three things this deliberately does not do:

- **A declared version still wins.** An existing manifest is untouched, and
    `pyproject.toml` is not consulted at all. This is opt-in by deletion.
- **jm never writes the resolved version back.** It is resolved when the
    manifest is read and dropped again when it is written, so a mutating command
    like `just-makeit object` cannot silently restore the key you removed.
- **`jm_version` is unaffected.** It pins the *tool*, not the project, and stays
    a literal.

If the manifest omits `version` and `pyproject.toml` supplies none either, the
default is still `"0.1.0"`.

The other copies (`CMakeLists.txt`, the `Doxyfile`, `native/src/<pkg>_lib.c`,
`bootstrap.toml`) are create-only and still carry their own literal, so they are
still checked -- see [`status`](commands/build.md#just-makeit-status).
`just-makeit config version X` writes `X` into `pyproject.toml` and each of
them, and leaves the manifest without a version (gh-2069).

### `[<object>]`

One section per standalone object or module-member object. The section name
is whatever you passed to `just-makeit object <name>`.

| Key           | Type                     | Default                                                       | Set by          |
| ------------- | ------------------------ | ------------------------------------------------------------- | --------------- |
| `arg_type`    | string                   | `"float _Complex"`                                            | `--arg-type`    |
| `return_type` | string                   | as [`--return-type`](commands/scaffold.md#just-makeit-object) | `--return-type` |
| `mutable`     | `"true"` or `"false"`    | `"false"`                                                     | `--mutable`     |
| `no_state`    | `"true"` or `"false"`    | `"false"`                                                     | `--no-state`    |
| `no_step`     | `"true"` or `"false"`    | `"false"`                                                     | `--no-step`     |
| `no_reset`    | `"true"` (only when set) | _(absent)_                                                    | `--no-reset`    |

These are the keys every object carries. The full list, with the CLI flag for
each, is [`[<component>]` keys](#component-keys) above.

### `[[<object>.state]]`

One entry per `--state` declaration.

| Key        | Type   | Notes                                                                         |
| ---------- | ------ | ----------------------------------------------------------------------------- |
| `name`     | string | ASCII letters/digits/underscores, no leading digit                            |
| `type`     | string | C type; append `[N]` for fixed arrays                                         |
| `default`  | string | C initialiser expression                                                      |
| `doc`      | string | The field's docstring, rendered verbatim (gh-1493)                            |
| `str_hint` | string | `T[N]` field only: `set_<name>` refuses a `str`, with this appended (gh-1761) |

### `[[<object>.array_args]]`

Fixed-size array constructor arguments added with `--array-arg`.

| Key    | Type   | Notes                                                                                                                                                         |
| ------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name` | string | Argument name                                                                                                                                                 |
| `type` | string | Stored as NumPy dtype name (`float32`, `float64`, `complex64`, …); C types (`float`, `double`, `float _Complex`, …) are also accepted on input and normalised |

A hand-written row may spell `type` as `dtype`. Those are the row's only keys:
any other is reported as an unknown key naming the row, and does nothing
(gh-2008). A constructor array that needs `rank` or `elements_per_sample` is
declared as an [`[[<object>.init_params]]`](#objectinit_params) row instead,
typed as a C array such as `"float[]"` (gh-2004).

### `[[group]]` and `[[<object>.init_groups]]`

A **field group** is a repeat declared once and instantiated under a prefix
(gh-999). jm's type vocabulary has no struct in either direction, so a C
descriptor built from N copies of the same small field set had to be flattened
into one long name-prefixed constructor list — written out once per repeat,
with every `default`, `doc` and `enum` binding duplicated N times and free to
drift, since jm saw N unrelated params.

```toml
[[enum]]
name   = "wfm_seq_kind"
values = ["literal", "lfsr"]

[[group]]
name = "wfm_seq"

[[group.fields]]
name    = "kind"
type    = "enum:wfm_seq_kind"
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
`(const void *, size_t)` pair via `y#`, gh-565). Both are manifest-only: they
have no `--init-param` spelling, so declare them in the table. The C
constructor must copy either borrow before returning.

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
selects a different `create_fn` instead of `<pkg>_<comp>_create`. Dispatch
picks one constructor, so it does not compose — jm refuses `optional` on more
than one array, and refuses it with no `create_fn`, pointing here in both
cases (gh-1004 / gh-1005). An init-param may also not be named
`<array>_len`, which is the length parameter jm derives for the array beside
it (gh-1002).

**dtype dispatch** is the other per-call choice. A 1-D array declaring
`real_type` and `real_create_fn` calls `real_create_fn` when the caller
passes an ndarray of exactly that dtype, and `<pkg>_<comp>_create` with the
declared type otherwise:

```toml
[[fir.init_params]]
name           = "taps"
type           = "float _Complex[]"
real_type      = "float[]"
real_create_fn = "fir_create_real"
```

Only a 1-D ndarray of the `real_type` dtype (here `float32`) reaches
`real_create_fn`. Everything else takes the default constructor and is
converted exactly as a plain array param of the declared type would be: a
list of floats, `None`, an array of another rank. numpy decides what
converts, as it does for any array param (gh-1826). Defaulted arrays compose
with it and reach either constructor as `NULL`/`0` when omitted (gh-1825).
It does not compose with a second dispatch or with an `optional` array, and
jm refuses both.

Every constructor the binding can call is declared in `<component>_core.h`
beside `create()`, with the same parameters except the swapped array, and the
scaffold stubs it in `_core.c` (gh-1827). That covers `real_create_fn` and an
`optional` array's `create_fn`, whose parameters are the array (its length or
two extents first, then the pointer) followed by `create()`'s.

### `[[<object>.methods]]`

One entry per `just-makeit method` call.

| Key               | Type                    | Notes                                                              |
| ----------------- | ----------------------- | ------------------------------------------------------------------ |
| `name`            | string                  | Method name                                                        |
| `arg_type`        | string                  | Array-style input type                                             |
| `return_type`     | string                  | C return type                                                      |
| `params`          | array of `{name, type}` | Named scalar / array parameters                                    |
| `variable_output` | bool                    | `--variable-output`                                                |
| `pass_capacity`   | bool                    | `--pass-capacity` (5-arg `(…, out, max_out)` C form)               |
| `exact_max_out`   | bool                    | `--exact-max-out`: `max_out` bounds any call, so allocate exactly  |
| `count_default`   | string                  | C expression seeding `count` for a void-input method (gh-657)      |
| `out_cols`        | string or int           | width of a matrix result: `(count / out_cols, out_cols)` (gh-2115) |
| `count_type`      | string                  | C type of a variable_output count (default `size_t`; gh-2012)      |
| `error_sentinel`  | string                  | C constant a `size_t` count refuses with (gh-2012)                 |
| `nogil`           | bool                    | `--nogil` (release the GIL across the kernel; see below)           |
| `status_return`   | bool                    | `int` return is a status: `-> None`, ValueError on non-0 (gh-432)  |
| `batch`           | bool                    | `--batch`                                                          |
| `multi_output`    | array of strings        | `--multi-output` types                                             |
| `out_type`        | string                  | `--out-type`                                                       |
| `out_divisor`     | int                     | `--out-divisor` (default `1`; omitted from TOML when `1`)          |

`nogil` wraps the pure-C kernel of a `variable_output`, `--single` or
`--borrow` method in `Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS` (numpy
accessors hoisted out first), so a thread-per-shard worker — one object +
output buffer per thread — scales across cores instead of serialising on the
GIL. Opt-in: it is sound only when the object is not shared across threads
concurrently (one object per stream).

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

[[agc.methods.params]]
name    = "tlm"
type    = "dp_tlm_t *"
capsule = "doppler.telemetry.dp_tlm"
header  = "telemetry/telemetry.h"

[[agc.methods.params]]
name = "prefix"
type = "const char *"

[[agc.methods.params]]
name    = "decim"
type    = "uint32_t"
default = "1"
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
`serializable` `set_state` glue. A module function's binding unwraps a
`capsule` param through the same parse builder, but `header` is read on a
method param only. A function spells a status return as
`check_return = true` (`jm function --check-return`).

### `[[<object>.properties]]`

One entry per `just-makeit property` call.

| Key                                     | Type   | Notes                                                                                                                                                           |
| --------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                                  | string | Property name                                                                                                                                                   |
| `type`                                  | string | C type of the value                                                                                                                                             |
| `writable`                              | bool   | `--writable`                                                                                                                                                    |
| `field`                                 | bool   | `--field` (adds struct member, auto-implements getter)                                                                                                          |
| `doc`                                   | string | `--doc`: the property's docstring, rendered verbatim                                                                                                            |
| `enum`                                  | string | `--enum`: decode the C int through a top-level `[[enum]]`                                                                                                       |
| `buf_field`, `len_field`, `valid_field` | string | `--buf-field` / `--len-field` / `--valid-field`: an ndarray view of a buffer field                                                                              |
| `expr`                                  | string | `--expr`: an inline C expression backs the getter                                                                                                               |
| `value_type`                            | string | `--value-type`: element type of a `dict` / `list` / `tuple` property                                                                                            |
| `count_fn`, `key_fn`, `value_fn`        | string | `--count-fn` / `--key-fn` / `--value-fn`: a container's C accessors (default `<pkg>_<comp>_num_<prop>`, `<pkg>_<comp>_<prop>_key`, `<pkg>_<comp>_<prop>_value`) |
| `capsule`, `capsule_type`               | string | `--capsule` / `--capsule-type`: publish a named PyCapsule, and what its pointer is                                                                              |
| `codec`, `entry_fn`, `entry_type`, …    | string | A codec container property — see [Variant codecs](#variant-codecs-codecname)                                                                                    |

### `[[<object>.views]]`

One entry per `just-makeit view` call — a **second Python class over the same
generated C core** (gh-504). The view shares `<pkg>_<comp>_state_t` and the
object's `_core.c`; only its constructor and its Python surface differ. Views
are a module-object feature, so the object must belong to a `[module.<name>]`.

| Key                                    | Type             | Notes                                                                                                                                                                        |
| -------------------------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `class_name`                           | string           | Python class name for the view. Required; unique across every class the module exposes.                                                                                      |
| `create_fn`                            | string           | C constructor the view's `__init__` calls. Required; must differ from `<pkg>_<comp>_create`. Scaffolded as a stub in the shared `_core.c`.                                   |
| `doc`                                  | string           | Docstring for the view class.                                                                                                                                                |
| `init_params`                          | array            | The view's own constructor params, same shape as `[[<object>.init_params]]`. Omit to inherit the parent's constructor shape.                                                 |
| `exclude_properties`                   | array of strings | Parent property names the view omits from its Python surface.                                                                                                                |
| `exclude_methods`                      | array of strings | Parent method names the view omits. Only the view's Python wrapper and `PyMethodDef` entry are dropped; the shared C function stays.                                         |
| `properties`                           | array            | The view's **own** properties, same shape as `[[<object>.properties]]`: a new name ADDS a property the parent lacks, a parent's name OVERRIDES it. Merged over the parent's. |
| `methods`                              | array            | The view's own methods, same shape as `[[<object>.methods]]`: ADD a new method (scaffolds a shared C stub) or OVERRIDE a parent method's doc.                                |
| `warnings`                             | array            | The view's own post-construction warnings, same shape as `[[<object>.warnings]]` (gh-509). A view carries no parent warnings, so this is its only source.                    |
| `create_error`, `create_error_message` | string           | The view's own `create()`-failure translation (gh-580). Absent, the view inherits the parent's.                                                                              |

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

The nested tables (and the view's `create_error`) are written by
`just-makeit property|method|warning|error <obj> --view <ClassName>`; see
[Extend commands → `just-makeit view`](commands/extend.md#just-makeit-view).

### `[module.<name>]`

| Key                                                      | Type                    | Notes                                                                                              |
| -------------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------- |
| `objects`                                                | array of strings        | Objects in declaration order                                                                       |
| `functions`                                              | array                   | Module-level functions (see below)                                                                 |
| `reexports`                                              | table `{sub = [names]}` | Re-export sibling symbols into `__init__.py` (0.15.1)                                              |
| `platforms`                                              | array of strings        | Build the extension on these platforms only (gh-1463)                                              |
| `package`                                                | string                  | Package directory the module's Python lands in; its classes import from `<pkg>.<package>` (gh-523) |
| `extra_link_libs` / `extra_include_dirs` / `extra_types` | array                   | Extra CMake wiring                                                                                 |
| `no_generate`                                            | string `"true"`         | Hand-written module: `jm apply` only wires the CMake `add_subdirectory`                            |
| `no_generate_reason`                                     | string                  | Why the module opts out; required with `no_generate` (gh-1313)                                     |
| `functions_in_core`                                      | string `"true"`         | Every function body in `<m>_core.c`, one TU (gh-247)                                               |
| `doc`                                                    | string                  | The module's docstring                                                                             |

### `[[module.<name>.functions]]`

One entry per `just-makeit function` call.

| Key                                                 | Type                          | Notes                                                                                                                                 |
| --------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                                              | string                        | Function name                                                                                                                         |
| `return_type`                                       | string                        | C return type                                                                                                                         |
| `doc`                                               | string                        | Python docstring                                                                                                                      |
| `params`                                            | array of `{name, type, out?}` | Parameters; an array param with `out = true` (or its synonym `mutable = true`) is writable — generated `T *name`, not `const T *name` |
| `inline`                                            | bool                          | `--inline`                                                                                                                            |
| `out_type`, `out_size`                              | string                        | `--out-type` / `--out-size`: the element type and length of a fresh output (`out_type = "str"` returns a `str`)                       |
| `variable_output`                                   | bool                          | `--variable-output`: the function sizes its own output                                                                                |
| `result_fields`, `max_results`, `max_results_param` | array / int / string          | A list-of-records result                                                                                                              |
| `check_return`                                      | bool                          | `--check-return`: a non-zero `int` return raises                                                                                      |
| `why`                                               | bool                          | `--why`: the C function takes a trailing `const char **why` (gh-1706)                                                                 |
| `status_errors`                                     | array                         | `--status-error`: status → exception table (gh-1614)                                                                                  |
| `impl`, `impl_file`, `replace`                      | string / table                | A body `jm apply` re-stamps — as for an [object](#component-lifecycle-impl-bodies)                                                    |

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

| Property key                                 | Default                     | Notes                                           |
| -------------------------------------------- | --------------------------- | ----------------------------------------------- |
| `codec`                                      | —                           | The `[codec.<name>]` to decode with             |
| `entry_fn`                                   | `<pkg>_<comp>_<prop>_entry` | Returns `const <entry_type> *(state, i)`        |
| `entry_type`                                 | `<pkg>_<comp>_<prop>_t`     | The entry struct type — **often needs setting** |
| `type_field` / `count_field` / `value_field` | `type` / `count` / `value`  | The discriminant / length / payload members     |

jm generates the decode helper (bytes → `str`; a numeric branch decodes `count`
elements to `int`/`float`, collapsing to a scalar when `count == 1` if the
codec declares `scalar_collapse`) and the
`dict[str, str | int | float | list[int] | list[float]]` `.pyi`. As on the
write side, jm declares **neither** `entry_fn` **nor** the entry struct — both
are your pure-C contract: declare them in the object's `_core.h`.

> **`entry_type` usually needs setting.** It defaults to
> `<pkg>_<comp>_<prop>_t` (property `keywords` on `wfm_reader` in project
> `my_project` → `my_project_wfm_reader_keywords_t`), but a shared,
> element-named struct (`wfm_keyword_t`) will not match that guess and the decode
> helper won't compile. Set `entry_type` explicitly whenever the struct isn't
> named after the property.

### Error surfaces

Codec errors are intentionally generic. On the write side a non-zero `sink_fn`
return raises `ValueError: <method> failed`; an unknown discriminant raises
`ValueError: unsupported code '<c>'`; a non-`str` value for a `bytes` code
raises `TypeError: value must be a str`; an empty sequence raises
`ValueError: value sequence is empty`; and a multi-character discriminant
string is a `PyArg`-level `TypeError`. On the read side an entry whose
discriminant no codec entry names raises `ValueError: unknown code '<c>'`. If
you need a domain-specific message, wrap the call
in Python.

______________________________________________________________________

## Type templates (`[template.<name>]`)

One declaration that stamps out the same object once per element type
(gh-1310) — for a family like a set of converters, where the siblings differ
only in a type, a class name and a constant:

```toml
[template.f32_to_int]
params = ["elem", "Elem", "scale"]   # the ONLY keys an instance may set
module = "cvt"                       # instances join this module
arg_type = "float"
return_type = "{elem}"
class_name = "F32To{Elem}"

[[template.f32_to_int.init_params]]
name = "scale"
type = "float"
default = "{scale}"

[[template.f32_to_int.instances]]
id = "f32_to_i16"
elem = "int16_t"
Elem = "I16"
scale = "32768.0f"

[[template.f32_to_int.instances]]
id = "f32_to_i8"
elem = "int8_t"
Elem = "I8"
scale = "128.0f"
```

Everything outside `params`, `module` and `instances` is an ordinary object
table, repeated for each instance with `{param}` filled in.

- **`params` is closed.** An instance row sets `id` and exactly those keys —
    nothing else. That rule is what stops siblings drifting apart: an instance
    has no way to disagree about `mutable` or grow a property the others lack. A
    genuine one-off difference means declaring that component outside the
    template.
- **`{param}` fills VALUES only**, never keys or table names, so the manifest
    parses and reads without expanding it. A slot naming no param is refused
    (`{id}`, the instance id, is always available).
- **Nothing is written per instance.** `load` expands the template and `save`
    folds it back, so no command writes the instances out as separate tables.
- **Instances are edited through the template.** `jm method`/`jm property`/
    `jm error`/`jm warning` on an instance are refused before anything is
    written. Declare the member on the template, where every instance gets it.
- **There is no CLI verb for a template.** `jm script` names it in a NOTE
    rather than replaying each instance as a `jm object`.

That makes the instances' **manifests** unable to diverge. To make their
**C** unable to diverge too, write it once, as a macro, and make the instances
`header_only` members of that family.

### One C family: `core_macro`

```toml
[template.f32_to_int]
params = ["elem", "sat"]
module = "cvt"
header_only = "true"
arg_type = "float"
return_type = "{elem}"
core_macro = "DECLARE_F32_TO_INT"            # defined in core_header
core_args = ["{id}", "{elem}", "{sat}"]      # its arguments, per instance
core_header = "my_project/cvt/f32_to_int.h"  # an #include spelling; yours
```

`native/inc/my_project/cvt/f32_to_int.h` is a header **you** write. It defines
`DECLARE_F32_TO_INT(id, elem, SAT)`, which expands to every function one
instance has (`create`, `destroy`, `reset`, `step`, `steps`, each accessor
and method), all `static inline`. It's the same pattern as a
`DECLARE_..._BUFFER(name, type)` macro. The declarations carry the C stem
(`<c_prefix>_<id>`) while `{id}` is the bare id, so under a `c_prefix` the
macro pastes the prefix on (`my_project_##id##_step`), or `core_args` passes
it already joined (`"my_project_{id}"`).

Each instance header then holds **declarations only**. Each one is
`static inline` and keeps its doc comment. After them comes one line that
supplies the definitions:

```c
/** @brief Process one input sample. ... */
static inline int16_t my_project_f32_to_i16_step(const my_project_f32_to_i16_state_t *state, float x);
...
DECLARE_F32_TO_INT (f32_to_i16, int16_t, 32767.0f)
```

- **All three keys or none**, and only with `header_only = "true"`. The macro
    defines the functions `static inline`, so there is no `_core.c`. Anything
    else is refused when the manifest is read.
- **jm writes no body for an instance**, at scaffold time or when `apply`
    adds a member the template gained. A member belongs in the family header.
    If you declare one and forget to define it, the build fails and names it
    (the generated link-check test, gh-1361).
- **`apply` keeps the invocation line in sync** with the instance row, the
    same way it keeps the prototypes in sync. Change `sat` and the next
    `apply` rewrites the line. Until then, `jm status --check` reports it. A
    line that's missing gets added. If the macro is invoked twice, `apply`
    leaves the header alone and warns, because it can't tell which one you
    meant. An invocation inside a comment (a `@code` example) is never taken
    for the real line.
- **The family header must exist before `apply`.** jm never writes it, so
    `apply` refuses, naming the file and the macro to define, rather than
    writing instances that include nothing.

Instances can still differ in prose, since each header's doc comments are its
own. That's deliberate: each instance keeps its own C API page and Python
docstrings. They can't differ in behaviour, because no instance header
contains a definition.

## Inspecting config

```sh
just-makeit config
```

Prints a summary of the project and every object's state variables:

```
project:  my_project
version:  0.1.0

engine:
  gain:  double = 1.0
  center_freq:  double = 1000.0
```

To update the version:

```sh
just-makeit config version 0.2.0
```

______________________________________________________________________

## Who owns a module's binding fragment

A standalone object's `<comp>_ext.c` is **glue**: jm renders it whole on
every `apply`, and `jm status --check` fails if it drifts. A module
object's `<mod>_ext_<obj>.c` is the same generated wrapper code, and is
**sacred** by default: created once, thereafter only ever gaining missing
members. So where a wrapper lives decides whether it receives fixes. The
exceptions are the units with nothing in them to author, which jm keeps
current in place: a `*_max_out` binding's arity, the `[[enum]]` tables, the
teardown wrappers of an object that declares `[<obj>.destroy]`, and a
record's dtype builders (gh-2055).

`fragment` on the object changes that:

```toml
[blk]
fragment = "generated"   # absent (the default) = "sacred", today's behaviour
```

With `fragment = "generated"`, that fragment becomes jm's content like any
other glue file. Anything hand-written belongs in the
`<mod>_ext_<obj>_extra.c` beside it, which jm already includes, and a
hand-written method is registered by an
[`[[<obj>.extra_methods]]`](#componentextra_methods-entries) row rather than a
row typed into the fragment's table.

### Checking before you flip

`adopt --check` answers "would this be safe", for every object at once, and
writes nothing:

```sh
just-makeit adopt --check                 # the whole project
just-makeit adopt --check --module dsp    # one module
```

Each object gets one of:

| verdict                 | meaning                                                                                                                                                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `generated`             | already flipped: the object declares `fragment = "generated"`                                                                                                                                                 |
| `would flip`            | every unit matches a fresh render                                                                                                                                                                             |
| `needs acknowledgement` | a unit's code differs, and jm cannot tell a hand-written body from a render predating a codegen change                                                                                                        |
| `REFUSES`               | a unit exists only on disk — the render does not produce it, so flipping would delete it — or a member's binding is ahead of the manifest (accepts more than it declares), so flipping would remove a feature |
| `TOKEN WITHOUT KEY`     | the file says jm regenerates it, but its object no longer declares `fragment = "generated"`, so `apply` leaves it alone: restore the key, or delete the file and re-run `jm apply`                            |

It exits non-zero when anything cannot flip unattended, so it works as a
ratchet in CI.

A **view** has no manifest table of its own, so its fragment is governed by
the key on its parent object and is reported under it. A refusal anywhere
in that set refuses the parent.

Under `needs acknowledgement`, each differing unit is listed one of two
ways:

| line        | meaning                                                                                                       |
| ----------- | ------------------------------------------------------------------------------------------------------------- |
| `adds only` | the render keeps every token of the unit and adds code (a guard, keyword arguments) — nothing on disk is lost |
| `differs:`  | the render would remove code from the unit — possibly code you wrote                                          |

### Flipping

`adopt` without `--check` makes the flip, for the objects you name — never
by default:

```sh
just-makeit adopt fir                          # one object
just-makeit adopt --module dsp                 # every object in a module
just-makeit adopt --all                        # every module object
just-makeit adopt fir --accept-additions       # take every `adds only` unit
just-makeit adopt fir --accept fn:Fir_init     # take one `differs:` unit, by name
```

An object flips, together with its views, only when nothing refuses it and
every differing unit is accepted: an `adds only` unit by `--accept-additions`
or by name, a `differs:` unit **only** by name. For each object that
flips, `adopt` writes `fragment = "generated"`, deletes its fragments and
runs `apply`, which renders them whole. For an object that does not flip,
nothing is written, and the units still waiting are listed. It exits
non-zero if any target did not flip.

Accepting is an act on the command line, not a manifest key. An `--accept`
that names no differing unit of the targets is refused as a typo before
anything is judged.

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

just-makeit new my_project \
    --c-prefix my_project
cd my_project

just-makeit object detector \
    --state threshold:float:0.5f
just-makeit object engine \
    --state gain:double:1.0 \
    --state center_freq:double:1000.0
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

    `--impl` / `--replace` lift a body into the sacred source **once** and
    record nothing in the manifest, so `script | sh` does not reproduce
    them. A body you want replayed belongs in the manifest's `impl` /
    `impl_file` / `replace` keys, which `jm apply` honours; `jm script` does
    not emit those either, so keep `just-makeit.toml` and your `_core.c` in
    version control.

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
    so keys placed after a `[[…]]` line become keys of that entry. jm warns
    that the entry carries an unknown key (`unknown state key …`), and the
    key has no effect.
- **Editing a signature** in TOML (removing a state variable, changing a
    method's return type) propagates to the glue files (`_ext.c`, `.pyi`,
    `CMakeLists.txt`) and the public declarations in `_core.h` on the next
    command, but the sacred `_core.c` body is left as you wrote it. Run
    `jm regenerate <obj>` to rebuild that component from the manifest.
    Hand-written bodies are lifted and spliced back by best-effort text
    matching; `--discard` resets to the bare scaffold. Either way,
    `git stash` first.
- **Don't rename the file** — `just-makeit` always looks for `just-makeit.toml`
    at the project root.
