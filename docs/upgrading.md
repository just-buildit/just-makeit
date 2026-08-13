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

### v0.28.9 — `jm regenerate` preserves hand-written bodies by default

Before v0.28.9, `jm regenerate` always deleted a component's sacred
`_core.c`/`_core.h` bodies and rebuilt blank stubs. As of v0.28.9 it lifts
create/destroy/reset/`step()`/getter/setter/method bodies out by function
name before deleting the files, and splices them back into the freshly
regenerated ones — the same by-name extract/restore machinery `jm apply`
uses to preserve hand-patched module `_ext.c` glue. Pass `--discard` for the
old clean-reset behavior. `jm add` and `jm remove --state` still pass
`discard=True` explicitly, since a state change means the old body's
signature is already stale.

The splice is best-effort text matching, not a guarantee — `git stash` (or
commit) first regardless. See
[Declarative scaffolding](declarative-scaffolding.md#jm-regenerate-component-the-deliberate-refresh)
for the full behavior.

No TOML changes, no `jm upgrade` required.

### v0.58 — clang-tidy, a shared test harness, and the compile database

Four related changes. **Every file involved is create-only**, so an existing
project receives none of them until you migrate it — and the migration is
three steps with three different mechanics, because `jm apply` reaches some of
these files and not others.

| what                                     | how it arrives                                                            |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| `.clang-tidy`                            | `jm apply`                                                                |
| `native/tests/jm_test.h`                 | `jm apply`                                                                |
| `make compile-commands` / `make tidy`    | delete `Makefile`, then `jm apply` — **destroys local edits, see step 0** |
| your existing `test_<comp>_core.c` files | by hand                                                                   |

#### 0. First: these files may be yours

`jm apply` never rewrites a create-only file, which is what makes the
delete-and-re-apply step below the only way to pick up new content — **and what
makes it destructive.** `jm_simd.h`, `jm_perf.h`, `jm_test.h`, `jm_bench.h` and
the Makefile are all files a project is invited to extend, and deleting one
throws away every local addition with it.

That is not hypothetical. doppler added a `JM_SUMSQ_F32` energy macro to its
`jm_simd.h` and lost it to this migration, which then failed the build at a
call site in its AGC — and it was reported as a just-makeit regression before
the history showed the macro had always been a local extension
([#954](https://github.com/just-buildit/just-makeit/issues/954)).

So before deleting anything:

```sh
git diff --stat HEAD -- native/inc/ native/tests/ native/benchmarks/ Makefile
git status --short          # untracked local headers count too
```

and after re-applying, diff again and copy your additions back. If a file has
local content you would rather not re-merge, add the new content by hand
instead — copy it from a freshly scaffolded project.

#### 1. `jm apply` — the two new files

```sh
jm apply
```

Materialises `.clang-tidy` and `native/tests/jm_test.h` because they are
missing. It will not overwrite either one afterwards, so you can edit both.

#### 2. The Makefile — delete it and re-apply

`jm apply` does **not** rewrite an existing `Makefile`, so the new
`compile-commands` and `tidy` targets do not arrive on their own:

```sh
rm Makefile && jm apply
```

**This discards any local edits to that Makefile.** Diff it first
(`git diff Makefile`) and re-apply your changes. If you have customised it
heavily, add the two targets by hand instead — copy them from a freshly
scaffolded project.

#### 3. Existing C tests — by hand

`test_<comp>_core.c` is yours; `jm` created it once and has never touched it
since. Each one still carries its own copy of `CHECK`, the counters and the
epilogue. To adopt the shared harness, replace the machinery at the top with:

```c
#define JM_TEST_NAME       "test_<comp>_core"
#define JM_SCAFFOLD_CHECKS 0
#include "jm_test.h"
```

and replace the closing block with `JM_TEST_EPILOGUE();`. Keep your
assertions. `REQUIRE(x)` is available for a precondition whose failure makes
everything after it meaningless.

Leave `JM_SCAFFOLD_CHECKS` at `0` in a test you have actually written — it is
the count `jm` generated, and `0` correctly says "none of these are mine".

**Why bother:** copies diverge. One downstream reached 90 definitions of
`CHECK` in 6 mutually incompatible variants, and in 20 files the failure gate
had drifted *above* later assertions — 75 assertions that could not affect the
exit code, which hid a real heap buffer overflow.

#### A caveat worth knowing before you start

**`jm status --check` will report your project up to date throughout all of
this.** That is not a bug you can work around, it is how the check is built:
`status` re-applies the manifest to a scratch copy and diffs, and `apply` does
not rewrite create-only files — so those files match in both trees no matter
how old they are. A clean `status` means "the files jm owns are current", not
"your scaffold is current".

#### Then run it

```sh
make compile-commands   # cmake's compile database, at the project root
make tidy               # clang-tidy over exactly the TUs cmake compiles
```

`jm` no longer writes `compile_commands.json` itself — cmake emits it, and
`make compile-commands` copies it where clangd and clang-tidy look. If your
project root has one that predates this change, delete it; it was hand-rolled,
incomplete, and is not refreshed by anything.

The shipped `.clang-tidy` sets `WarningsAsErrors: "*"`, so `make tidy` is a
gate. A **freshly scaffolded** project reports clean. An existing one very
likely will not on the first run — that is the point, and the findings are
about your code, not the scaffold. If a toolchain upgrade later adds a check
that fires on generated code, comment that line out rather than working around
the diagnostic: the file is yours, and `jm` will not rewrite it.

No TOML changes, no `jm upgrade` required.

______________________________________________________________________

## For project maintainers

If you ship a library built on `just-makeit` and your users upgrade jm
independently, remind them to run `just-makeit upgrade` after updating the
tool. The warning printed by commands like `just-makeit object` is there to
catch this automatically.
