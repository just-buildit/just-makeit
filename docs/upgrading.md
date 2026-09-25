# Upgrading an existing project

!!! tip "A worked upgrade, end to end"

    [Upgrading an old project](examples/stale_project.md) takes a project
    jm 0.33.14 generated to the current jm one command at a time — `status`,
    `apply`, `upgrade`, receiving a fix a binding fragment is missing, adopting
    the files jm ships newer — and proves the result builds and runs. This page
    covers `jm upgrade`, one of those steps.

When a new version of `just-makeit` ships features that change the project
scaffold — new files, new `just-makeit.toml` keys, new build targets — existing
projects do not automatically get those additions. The upgrade system handles
this safely and idempotently.

______________________________________________________________________

## How it works

Every `just-makeit.toml` carries a `schema` version number:

```toml
[project]
name    = "my_dsp"
version = "0.1.0"
schema  = "8"
```

When `just-makeit` itself is updated, `CURRENT_SCHEMA` advances;
`jm upgrade` prints the schema it moves your project from and to. If your
project's schema is behind, `just-makeit` will remind you whenever you run a
command that modifies the project:

```
warning: project schema is v4, current is v8.
Run 'just-makeit upgrade' to get new features.
```

Running `just-makeit upgrade` applies every pending migration in order, then
updates `schema` in `just-makeit.toml`.

______________________________________________________________________

## Running the upgrade

From your project root:

```sh
# 1. adopt jm's new clib_common.h -- take the render as-is; do not hand-edit
#    it. `jm status --check` names it as OUTDATED.
# 2. re-spell your own C.
jm upgrade
```

`jm upgrade` names every file it changed. It is **idempotent** — a second run
is a silent no-op, and so is every run on a project scaffolded by jm 0.74.0 or
later.

It also renames any file jm now writes under a new name — today that is
`jb.toml`, which became `bootstrap.toml` — as a **rename**, so what you added
to it comes along. Until you run it, `jm apply` will not create the new file
beside the old one, and `jm status` lists the old one as SUPERSEDED. If both
already exist (an older jm created the new one beside yours), nothing is
renamed: merge your additions into `bootstrap.toml` and delete `jb.toml`.

**Do both steps or neither.** Adopting the new `clib_common.h` without
re-spelling leaves component headers that no longer parse from C++ at all,
which is the failure gh-1148 fixed — strictly worse than where you started.

The re-spelling touches **code only**: comments and string literals are left
exactly as written (gh-1382). They are prose, and a comment that *quotes* the
old spelling while explaining why it was a problem would otherwise be
rewritten into a false statement.

Then rebuild and run your tests. `complex` typed by **you** is still accepted
everywhere jm reads a type — in `just-makeit.toml`, on the CLI, and in a header
`jm bind` parses; it is resolved to `_Complex` before anything is rendered.

No TOML changes, no `jm upgrade` required.

______________________________________________________________________

______________________________________________________________________

## Headers under your package (gh-1583)

A project's headers live under `native/inc/<pkg>/`, and every include of one
is spelled `"<pkg>/..."`:

```c
#include "my_dsp/fir/fir_core.h"    /* was: "fir/fir_core.h" */
#include "my_dsp/clib_common.h"     /* was: "clib_common.h" */
```

`native/inc` is still the `-I` directory, so an installed project's headers
land in `include/<pkg>/` and two jm projects in one prefix no longer collide
over a component name, `clib_common.h`, or `jm_perf.h`.

`jm upgrade` (schema 7 to 8) moves an existing project there. It moves all of
`native/inc/*` one level down, then respells every reference that **resolves
to a moved file**:

- an `#include` in any of your C or C++ files, sacred ones included. A quoted
    include that finds a *different* file beside itself is left alone, since C
    finds that one first;
- a quoted string in `just-makeit.toml` or a fragment it includes: a
    `header =`, a `core_header =`, and the like, which jm writes into an
    `#include` verbatim;
- a `native/inc/...` path in any `CMakeLists.txt` or `.cmake` file.

A reference that does not resolve to a moved file (a vendored library's own
`"config.h"`, a system header) is not touched. `jm upgrade` prints every file
it changed. Review them, then run `jm apply` and rebuild.

A header **you** add from now on goes under `native/inc/<pkg>/` too, and a
manifest `header =` naming it is spelled `"<pkg>/..."`.

______________________________________________________________________

## One name per library (gh-1581)

A library's `.pc` is named for the library: `libmy_proj` ships
`my_proj.pc`, not `my-proj.pc`, and its exported CMake targets are
`my_proj::my_proj` and `my_proj::my_proj-static` (they were
`my_proj::my_proj_lib` and `..._lib_static`). A name without `_` is
unaffected. Update what your consumers write:

```sh
pkg-config --cflags --libs my_proj           # was: my-proj
target_link_libraries(app PRIVATE my_proj::my_proj)   # was: my_proj::my_proj_lib
```

In the project, `jm upgrade` renames `cmake/my-proj.pc.in` to
`cmake/my_proj.pc.in` (its edits, and jm's ownership of it, come along);
until then `apply` leaves both names alone and `status` names the old one.

## Packaging (`adopt --packaging`)

Three things carry what a C consumer reads through pkg-config and
`find_package`: `cmake/<pkg>.pc.in`, `cmake/<pkg>-config.cmake.in`, and the
install section of the root `CMakeLists.txt`, which installs the library, its
headers and both files. Since gh-1589 jm owns all three:

- a new project's templates start with `# jm:generated <file>`, and its
    install section runs from `# ── Install` to `# ── End install`;
- `jm apply` renders them, so every packaging fix arrives with an upgrade.

A project scaffolded earlier has neither mark, and `apply` never touches what
it has. `jm status` lists each template that is behind under **PACKAGING**,
and the install section as `ROOT CMAKE install-block`.

```sh
jm adopt --packaging --check   # the diff, and anything of yours adopting would drop
jm adopt --packaging           # hand them to jm
```

Adopting drops nothing a released jm rendered -- jm records every line and
command it ever shipped there -- so an older project's own copies adopt
cleanly. A line or command of yours is refused and named until you move it
(or accept losing it) -- see
[`just-makeit adopt --packaging`](commands/build.md#just-makeit-adopt-packaging).

______________________________________________________________________

## For project maintainers

If you ship a library built on `just-makeit` and your users upgrade jm
independently, remind them to run `just-makeit upgrade` after updating the
tool. The warning printed by commands like `just-makeit object` is there to
catch this automatically.
