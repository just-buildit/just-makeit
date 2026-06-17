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
schema  = "6"
```

When `just-makeit` itself is updated, `CURRENT_SCHEMA` advances (it is `6`
as of v0.14). If your project's schema is behind, `just-makeit` will remind
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
just-makeit upgrade
```

Example output for a schema 1 → 2 migration:

```
migrating schema 1 → 2
  create  zensical.toml
  create  docs/index.md
  create  docs/api.md
project is now at schema 2
```

If the project is already current:

```sh
$ just-makeit upgrade
already up to date (schema 7)
```

______________________________________________________________________

## Safety guarantees

- **Existing files are never overwritten.** If `zensical.toml` or `docs/index.md`
    already exist — whether you created them manually or a previous run wrote them —
    the upgrade skips those files silently.
- **`just-makeit.toml` keys are only added, never removed.** If a key the
    migration would add is already present (even with a different value), it is
    left untouched.
- **Idempotent.** Running `just-makeit upgrade` twice is safe — the second run
    is always a no-op.

______________________________________________________________________

## What each migration adds

| Migration | Adds                                                                                                          |
| --------- | ------------------------------------------------------------------------------------------------------------- |
| 1 → 2     | Docs scaffolding: `zensical.toml`, `docs/index.md`, `docs/api.md`.                                            |
| 2 → 3     | Regenerates bench files so per-method timing blocks appear in older projects.                                 |
| 3 → 4     | Adds `native/benchmarks/jm_bench.h` (per-round stats + pytest-benchmark JSON); regens benches.                |
| 4 → 5     | Moves benchmarking under `just-makeit bench`; writes dated snapshots to `benchmarks/history/`.                |
| 5 → 6     | Gates the `include = [...]` split-manifest key; the version bump alone is the migration.                      |
| 6 → 7     | Gates the top-level `[[enum]]` SSOT and `type = "enum:<name>"` refs; the version bump alone is the migration. |

The schema 1 → 2 files are starter stubs — edit them freely after the
upgrade. See [`make docs` and `make coverage`](commands/build.md) for the
build targets that use them.

______________________________________________________________________

## Behavioral changes (no schema bump required)

These changes affect how the CLI tools behave but do not change
`just-makeit.toml` or require running `jm upgrade`.

### v0.14 — additive verbs are now splice-free

Before v0.14, `jm method`, `jm property`, and `jm function` re-rendered
`<comp>_core.c` and `<comp>_core.h` from the manifest and grafted your
hand-written bodies back using a brace-matching splicer. As of v0.14 they
are **additive and splice-free**: they inject a declaration into
`<comp>_core.h` and append a fresh stub to `<comp>_core.c` — the existing
bodies are never touched.

**What this means for existing projects:**

- Running `jm method`, `jm property`, or `jm function` on a v0.14 project
    is safer than before — no risk of the splicer mis-merging your code.
- **`jm add` and structural changes** (adding/removing state fields, changing
    `arg_type`) now route through `jm regenerate` instead of the old splicer.
    `jm regenerate` deletes every file the component owns and rebuilds from
    the manifest — `git stash` your `_core.c` first.
- The sacred/glue contract (see [Declarative scaffolding](declarative-scaffolding.md#the-sacredglue-contract))
    is now enforced mechanically, not by a fragile regex pass.

No TOML changes, no `jm upgrade` required.

______________________________________________________________________

## For project maintainers

If you ship a library built on `just-makeit` and your users upgrade jm
independently, remind them to run `just-makeit upgrade` after updating the
tool. The warning printed by commands like `just-makeit object` is there to
catch this automatically.
