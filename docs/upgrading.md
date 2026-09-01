# Upgrading an existing project

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

**Do both steps or neither.** Adopting the new `clib_common.h` without
re-spelling leaves component headers that no longer parse from C++ at all,
which is the failure gh-1148 fixed — strictly worse than where you started.

`clib_common.h` itself is skipped by the re-spelling, deliberately: jm's render
of it is already correct, and its comment *quotes* the old spelling while
explaining why that spelling was a problem. A blanket pass would rewrite that
prose into a false statement.

Then rebuild and run your tests. `complex` typed by **you** is still accepted
everywhere jm reads a type — in `just-makeit.toml`, on the CLI, and in a header
`jm bind` parses; it is resolved to `_Complex` before anything is rendered.

No TOML changes, no `jm upgrade` required.

______________________________________________________________________

______________________________________________________________________

## For project maintainers

If you ship a library built on `just-makeit` and your users upgrade jm
independently, remind them to run `just-makeit upgrade` after updating the
tool. The warning printed by commands like `just-makeit object` is there to
catch this automatically.
