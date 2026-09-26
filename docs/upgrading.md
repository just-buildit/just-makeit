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
jm upgrade    # applies every pending migration, printing each file it changed
jm apply      # regenerates the glue against the migrated tree
```

Then review what `jm upgrade` printed, rebuild and run your tests.

`jm upgrade` is **idempotent** — a second run is a silent no-op. What a
migration does is described in its own section below; the one most projects
meet first is [Headers under your package](#headers-under-your-package-gh-1583).

It also renames any file jm now writes under a new name — today that is
`jb.toml`, which became `bootstrap.toml` — as a **rename**, so what you added
to it comes along. Until you run it, `jm apply` will not create the new file
beside the old one, and `jm status` lists the old one as SUPERSEDED. If both
already exist (an older jm created the new one beside yours), nothing is
renamed: merge your additions into `bootstrap.toml` and delete `jb.toml`.

______________________________________________________________________

## The `_Complex` spelling (gh-1148)

A project scaffolded before jm 0.74.0 spells complex types with the
`<complex.h>` macro `complex`, which does not parse from C++. `jm upgrade`
re-spells your own C to `_Complex`; it runs on every `jm upgrade`, whatever the schema. Adopt
jm's new `clib_common.h` alongside it — take the render as-is, do not
hand-edit it; `jm status --check` names it as OUTDATED.

**Do both or neither.** Adopting the new `clib_common.h` without
re-spelling leaves component headers that no longer parse from C++ at all,
which is the failure gh-1148 fixed — strictly worse than where you started.

The re-spelling touches **code only**: comments and string literals are left
exactly as written (gh-1382). They are prose, and a comment that *quotes* the
old spelling while explaining why it was a problem would otherwise be
rewritten into a false statement.

It covers the C in your manifest too: an `impl`, `create_impl`,
`reset_impl` or `destroy_impl` body is respelled in place, the same way
(gh-1647). jm renders a header body *from* that string, so respelling only
the header would have had the next `jm apply` put the old spelling back --
and every `jm upgrade` after it report the same file again.

`complex` typed by **you** is still accepted everywhere jm reads a type — in
`just-makeit.toml`, on the CLI, and in a header `jm bind` parses; it is
resolved to `_Complex` before anything is rendered.

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

**Your consumers change too.** Code outside the project that includes an
installed jm project's headers — a C program, a second library — spells them
the new way once the project is released with schema 8:

```c
#include <my_dsp/fir/fir_core.h>    /* was: <fir/fir_core.h> */
```

Nothing else in the consumer moves: `pkg-config --cflags` and
`find_package` still add `include/`, not `include/<pkg>/`, which is what makes
the prefix part of every spelling. See [Installing your C library](c-library.md).

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

## Symbol prefix (gh-1591)

`[project] c_prefix` renames every C symbol jm derives
([c-library](c-library.md#two-packages-one-program)). **`jm new` writes one
by default** -- the package name -- so a project created now is prefixed
from its first file (`--no-c-prefix` opts out). **A project created before
keeps its bare names**: nothing changes until you set the key and run
`jm upgrade`, and an upgrade to this jm never adds it for you. On an existing project
the key renames symbols your own C already calls, so until that C follows,
`apply` refuses -- naming each file and the old names it still spells -- and
writes nothing. `jm upgrade` moves it:

```sh
# 1. set the key in just-makeit.toml:   [project]  c_prefix = "dp"
jm upgrade    # respells your C, then prints each file and the rename table
# 2. review the files it printed
jm apply      # regenerates the glue onto the prefix
# 3. rebuild and run your tests
```

The rename set is not a pattern: it is exactly the identifiers jm's render
of your manifest declares prefixed -- `fir_create`, `fir_state_t`,
`FIR_CORE_H`, a method's `fir_<name>`, a module function `mix` -- each old
spelling to its new one, plus every property's `fir_get_<name>` whether jm
declares it or not: a `field = true` property's docstring is read from the
getter you declare to document it (gh-1670). It is rewritten:

- in **code only**: a comment or string literal that quotes `fir_create`
    keeps it (gh-1382);
- as **whole identifiers, case-sensitively**: `fir_state_t` moves; your own
    `FIR_STATE_MAGIC`, or a `fir_create_default` you wrote, does not;
- in every C and C++ file of the project -- the sacred `_core.h` /
    `_core.c`, module function sources, tests, benchmarks, `native/examples/`
    -- but not in a **nested project** (a directory with its own
    `just-makeit.toml`), which its own `jm upgrade` moves;
- in the **C your manifest holds** (gh-1653), which jm renders into the C
    again whenever it re-renders from the manifest: every `*_impl` body, and
    every state, init-param and method-param `type` -- a `depends_on`
    sibling's `lo_state_t *` becomes `dp_lo_state_t *` -- and every C
    expression jm splices into the binding (gh-1666): a module function's
    `out_size` calling a sibling (`kaiser_num_taps(...) | 1`), a property's
    `expr`, a method's `count_default`, an object's `init_post_parse`, an
    init-param's `default_raw`. The full set is `_csym.MANIFEST_C_KEYS`,
    and `apply`'s refusal reads the same one. A `default` is C too
    (gh-1671) -- a state field's `default = "sizeof(lo_state_t)"`, a
    scalar parameter's -- and moves, except on an entry that names an enum
    (an `enum` key, or a `type` of `enum:<name>` / `string_enum:...`),
    whose default is a choice string and, like the enum `type` itself, is
    never touched. A `replace = { "<old>" = "<new>" }` table moves too (gh-1656),
    inline or as `[<comp>.replace]`: each value is C spliced into the
    `impl` body, and each key is matched against that body, so it moves
    with it -- unless the body is lifted from an `impl_file` the upgrade
    does not respell, where the key keeps the spelling that file still
    has. Each value is replaced where it stands, so the file's layout and
    comments are kept.
    An `*_impl_file = "path::fn"` whose file is one of the above has its
    `fn` follow the file. Keys naming a function you wrote (`fn`,
    `create_fn`, ... -- `_csym.AUTHOR_NAMED_KEYS`) are never touched; when
    one names a symbol the prefix renames -- a handle's
    `create_fn = "lo_create"` over component `lo` -- `apply` and `upgrade`
    refuse before writing anything (gh-1671), naming the file, the key and
    the new spelling to write there yourself;
- only where the name **refers** to jm's symbol (gh-1668): a call, `&fir_bits`,
    a function pointer, a type use, the function's own declaration. A struct
    member, a member access (`.fir_bits`, `->fir_bits`), a designated
    initializer, a parameter or a local spelled like a derived name is yours,
    and keeps its spelling -- as does every use of that parameter or local
    in its scope;
- in a call to a macro that **token-pastes** a stem into derived names
    (gh-1669): jm's `JM_DEFINE_STEPS (fir, ...)`, which pastes `fir_step` /
    `fir_steps` / `fir_step_batch`, or your own `#define T(pfx, s)   pfx##_reset (s)` in any project C file. That argument moves, and so does
    your `fir_step_batch`. When your macro pastes the same argument into a
    derived name AND one of yours (`pfx##_reset`, `pfx##_mine`), no one
    spelling is right: `upgrade` and `apply` refuse, naming the call. The
    bare stem moves nowhere else -- `fir` is also a file, a directory and a
    Python name.

A second `jm upgrade` changes nothing. It prints the table as `old<TAB>new`
lines, so the code jm does not own can follow from it:

```sh
jm upgrade | awk -F'\t' 'NF == 2'     # the rename table, as a TSV
```

It will NOT touch: the body of your own macros, another language's FFI declarations (a
Rust `extern "C"` block), C in documentation code fences, or a nested
project. Respell those from the table.

Changing a prefix that is already applied (`a` to `b`), or removing the key
from a prefixed tree, is not migrated: `apply` and `upgrade` refuse it
before writing anything (gh-1660), naming the component and the prefix its header already carries (gh-1650).

A prefix can derive a name your C **already uses**: with `c_prefix = "dp"`,
component `syncword`'s method `find` derives `dp_syncword_find` -- and if
you already wrote a `dp_syncword_find`, two different functions would
become one. Worse, the respell would turn your wrapper's call to
`syncword_find` into a call to itself. `apply` and `upgrade` refuse this
before writing anything (gh-1657), naming each file and line, the name, and
the component and method (or module function) it derives from:

```
error: native/inc/dp_syncword.h:99 already declares `dp_syncword_find`, the
name [project] c_prefix = 'dp' derives from component `syncword`'s method
`find` -- two different C symbols would become one. Rename yours, or choose
another c_prefix
```

The same holds for the name as it is spelled today (gh-1661). A file with its
own `static crc16`, beside a module function `crc16`, would have it renamed
to `dp_crc16` too: the respell goes by name, and cannot tell your calls from
jm's. That is refused the same way: the message says the file declares its
own `crc16`.

Rename your symbol out of the way (or make it `static` under another name),
or pick another prefix. A name counts only where it is declared outside the
files jm itself declares it in, so a tree already partly moved onto the
prefix is not refused, and a project with no `c_prefix` is never asked: with
nothing renamed, the two never meet.

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
