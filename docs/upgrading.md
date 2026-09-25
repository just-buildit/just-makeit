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
schema  = "7"
```

When `just-makeit` itself is updated, `CURRENT_SCHEMA` advances (it is `7`
currently). If your project's schema is behind, `just-makeit` will remind
you whenever you run a command that modifies the project:

```
warning: project schema is v4, current is v7.
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
