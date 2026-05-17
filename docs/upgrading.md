# Upgrading an existing project

When a new version of `just-makeit` ships features that change the project
scaffold — new files, new `just-makeit.toml` keys, new build targets — existing
projects do not automatically get those additions.  The upgrade system handles
this safely and idempotently.

______________________________________________________________________

## How it works

Every `just-makeit.toml` carries a `schema` version number:

```toml
[project]
name    = "my_dsp"
version = "0.1.0"
schema  = "2"
```

When `just-makeit` itself is updated, `CURRENT_SCHEMA` advances.  If your
project's schema is behind, `just-makeit` will remind you whenever you run a
command that modifies the project:

```
warning: project schema is v1, current is v2.
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
already up to date (schema 2)
```

______________________________________________________________________

## Safety guarantees

- **Existing files are never overwritten.**  If `zensical.toml` or `docs/index.md`
  already exist — whether you created them manually or a previous run wrote them —
  the upgrade skips those files silently.
- **`just-makeit.toml` keys are only added, never removed.**  If a key the
  migration would add is already present (even with a different value), it is
  left untouched.
- **Idempotent.**  Running `just-makeit upgrade` twice is safe — the second run
  is always a no-op.

______________________________________________________________________

## What each migration adds

### Schema 1 → 2

Adds documentation scaffolding.

| File            | Purpose                                              |
| --------------- | ---------------------------------------------------- |
| `zensical.toml` | [Zensical](https://zensical.org) docs configuration. `make docs` builds the site; `zensical serve` hot-reloads it. |
| `docs/index.md` | Project home page stub.                              |
| `docs/api.md`   | Auto-generated Python API reference page.            |

These files are starter stubs — edit them freely after the upgrade.  See
[`make docs` and `make coverage`](commands/build.md) for the build targets
that use them.

______________________________________________________________________

## For project maintainers

If you ship a library built on `just-makeit` and your users upgrade jm
independently, remind them to run `just-makeit upgrade` after updating the
tool.  The warning printed by commands like `just-makeit object` is there to
catch this automatically.
