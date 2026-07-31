# just-makeit — Developer Guide

This document is the entry point for working on `just-makeit` itself.
It covers the project layout, how the code is organized, the test suite,
and the release process.

______________________________________________________________________

## What is just-makeit?

`just-makeit` is a Python CLI tool that scaffolds Python C extension projects.
One command generates a complete, working project: core C library, thin Python
binding, CMake build system, and full test coverage — all passing before you
write a single line of code.

**Key idea:** At the C level, every "object" is the same structure:
`_core.h` (API + inline `step()`), `_core.c` (lifecycle + block processor),
and a CMake OBJECT library that compiles once and links into both the Python
`.so` and the combined C shared library.

The only thing that varies is the Python packaging layer:

- **Standalone** (`object <name>`) — its own `.so`, imported as `from pkg import Name`
- **In-module** (`object <name> --module mod`) — shares a `.so` subpackage, imported as `from pkg.mod import Name`

______________________________________________________________________

## Repository layout

```
just-makeit/
├── src/just_makeit/          # the CLI package
│   ├── _cli.py               # argument parsing and dispatch
│   ├── _cli_*.py             # per-command argument parsers
│   ├── _new.py               # `new` command — project scaffold
│   ├── _object.py            # `object` command — add a type (standalone or in-module)
│   ├── _init.py              # internal: standalone object file generation
│   ├── _module.py            # `module` command — scaffold empty extension module
│   ├── _method.py            # `method` command — named execute variants
│   ├── _property.py          # `property` command — Python properties
│   ├── _function.py          # `function` command — module-level C functions
│   ├── _add.py               # `add` command — append state vars to existing object
│   ├── _perf.py              # `perf` command — add performance annotations
│   ├── _impl.py              # `--impl` body lifting (funcname or line range)
│   ├── _apply.py             # `apply` command — sacred/glue materialize from TOML
│   ├── _regenerate.py        # `regenerate` command — rebuild a component's files
│   ├── _remove.py            # `remove` command — delete + strip TOML/CMake wiring
│   ├── _bind.py              # `bind` command — synthesise _ext.c from a _core.h
│   ├── _build.py             # `build`/`test`/`dry-run` commands
│   ├── _config.py            # just-makeit.toml read/write
│   ├── _render.py            # render engine + template constants loaded from templates/
│   ├── _context/             # make_*_ctx() context builders
│   ├── templates/            # the real template files (c/, cmake/, py/, make/, toml/, …)
│   ├── _scripts.py           # entry points: jm-install-deps, jm-run-tests, jm-docker-e2e
│   └── scripts/              # bundled shell utilities (shipped in wheel)
│       ├── install-deps.sh   # OS-aware dep installer + venv setup
│       └── docker-e2e.sh     # Docker end-to-end smoke test
├── tests/                    # pytest suite
│   ├── test_new.py           # `new` command integration tests
│   ├── test_init.py          # internal `_init.run()` tests (standalone path)
│   ├── test_cli.py           # CLI dispatch tests (subprocess)
│   ├── test_add.py           # `add` command tests
│   ├── test_perf.py          # `perf` command tests
│   ├── test_templates.py     # template rendering unit tests
│   ├── test_config.py        # config load/save tests
│   ├── test_example_*.py     # end-to-end example tests (cmake + build)
│   └── bench_scaffold.py     # pytest-benchmark for scaffold generation speed
├── docs/                     # MkDocs source
│   ├── developers/           # this directory
│   ├── examples/             # per-example walkthroughs
│   └── *.md                  # commands, workflow, types, perf, c-library
├── examples/                 # repo-root README only; the worked examples
│                             #   now live in src/just_makeit/examples/ (dozens,
│                             #   bundled in the wheel as package data)
├── scripts/
│   ├── copy_examples.py      # copies example sources into docs/
│   └── sync_version.py       # keeps version strings in sync across files
├── .github/workflows/
│   ├── ci.yml                # runs tests on every push to main / PR
│   ├── release.yml           # triggered by v* tag: test → build → publish to PyPI
│   ├── artifact.yml          # post-release smoke test: installs from PyPI, builds real projects
│   └── docs.yml              # builds and deploys MkDocs site
├── CHANGELOG.md
├── pyproject.toml
└── uv.lock
```

______________________________________________________________________

## How the code fits together

### Command flow

```
just-makeit <cmd>
    └── _cli.py:main()
            ├── new       → _new.run()
            │                  └── _object.run(module=None)  ← if --object given
            │                       └── _init.run()           ← standalone path
            ├── object    → _object.run()
            │                  ├── module=None  → _init.run()   (standalone)
            │                  └── module=name  → in-module path
            ├── module    → _module.run()
            ├── method/property/function → _method.*/_property.*/_function.*
            ├── add       → _add.run()
            ├── perf      → _perf.run()
            ├── apply     → _apply.run()        (sacred/glue materialize)
            ├── regenerate→ _regenerate.run()   (delete + re-apply a component)
            ├── remove    → _remove.run()
            ├── bind      → _bind.run()
            └── build/test/dry-run → _build.*
```

### Templates and rendering

Generated file content lives as real files under
`src/just_makeit/templates/` (`c/`, `cmake/`, `py/`, `make/`, `toml/`,
`doc/`, `misc/`). `_render.py` loads each at import time and substitutes
`<<placeholder>>` tokens (C/H templates wrap them as `/*<<token>>*/` so
clang-format can still parse the file). The context dict is built by the
`make_*_ctx()` functions in `_context/`.

**To change what generated files look like:** edit the relevant file
under `templates/`, not a Python string constant.

### Config (`_config.py` + `just-makeit.toml`)

`just-makeit.toml` is the source of truth for scaffolded state. It records:

- `[project]` — name, version, build system, perf flag
- `[<comp>]` — state vars, arg/return types, for each standalone object
- `[module.<name>]` — objects list for each module

`_config.py` provides typed accessors (`components()`, `modules()`,
`module_objects()`, `state_vars()`, `arg_type()`, …).

______________________________________________________________________

## Development setup

```sh
git clone https://github.com/just-buildit/just-makeit
cd just-makeit
make setup
```

`make setup` syncs the `dev` dependency group and installs the git hook. Plain
`uv sync` does neither, which leaves you without ruff, mypy or pre-commit and
with lint failing for the first time in CI.

Then:

```sh
make test           # unit + integration
make test-examples  # slow: scaffolds, builds with cmake, runs
make bench          # scaffold generation benchmarks
make lint           # the gate CI runs — exactly this
make format         # fix formatting
```

**Run these through `make`, not directly.** The targets are not aliases for the
obvious command: `make test` runs pytest in a deliberately isolated
`--no-project` environment so the suite exercises the installed-package path,
which a bare `uv run pytest` does not. The Makefile is the single source of
truth for *how* every tool runs; `pyproject.toml` owns which versions, and
`.pre-commit-config.yaml` dispatches back in via `make -s lint-<tool>`. Calling
a tool directly gets you a different environment than CI, silently.

### Where the targets come from

`make help` is generated from the targets that actually exist — it is the one
list that cannot go stale, so prefer it over any list written in prose.

The shared ones live in `standard.mk`, vendored from the cross-org standard at
<https://just-buildit.github.io/standard.mk>; the `Makefile` holds only this
repo's configuration, and `local.mk` holds targets unique to it. **Do not edit
`standard.mk`** — it is vendored verbatim and `make lint` fails on any
difference from canonical. Per-repo variation is a variable in `Makefile`; a
shared change goes to canonical and comes back by re-vendoring. Usage and the
full contract are documented beside the file itself.

______________________________________________________________________

## Git workflow

All non-trivial changes — new features, bug fixes, refactors, docs — go through
a branch and a PR. Direct pushes to `main` are reserved for release mechanics
(version bump + CHANGELOG commit, see [release-checklist.md](release-checklist.md)).

### Branch naming

| Prefix   | Use                                    |
| -------- | -------------------------------------- |
| `feat/`  | new command, flag, or generated output |
| `fix/`   | bug fix                                |
| `docs/`  | documentation only                     |
| `chore/` | tooling, CI, deps, version bump        |

```sh
git checkout -b feat/out-type-scalar-param
# ... make changes, commit ...
gh pr create --fill
```

### PR rules

- CI must be green before a PR can enter the merge queue (`ci.yml` runs on
    every PR *and* on the merge queue's batched commit).
- Keep PRs focused — one logical change per PR makes bisect and revert easy.
- The PR title becomes the CHANGELOG entry; write it accordingly
    (`fix: jm apply drops extra_link_libs on regeneration`).

### Merging

PRs land through the **merge queue**. Once CI is green, choose "Merge when
ready" to add the PR to the queue: GitHub rebases it onto the latest `main`,
runs the full CI on that batched commit (the required "CI passed" check), and
**squash-merges automatically** when green. You never have to manually rebase a
PR just because another landed ahead of it — the queue keeps things up to date
by construction. The branch is deleted on merge; history stays linear.

### What goes directly on `main`

Only two things skip the PR process:

1. **Release bump** — `chore: bump to X.Y.Z` (pyproject.toml + CHANGELOG only).
1. **Hotfix** — a one-liner fix that is urgent and trivially correct (rare).

______________________________________________________________________

## Adding a new template / feature

1. Add or edit the template file under `src/just_makeit/templates/`.
1. Update the context builder in `_context/` if new placeholder keys are needed.
1. Wire the new file into the relevant `run()` function (`_new.py`, `_object.py`, `_init.py`).
1. Add tests in `tests/test_new.py` or `tests/test_init.py`.
1. Update the relevant page in `docs/commands/` and any relevant workflow docs.

______________________________________________________________________

## CI overview

| Workflow       | Trigger                | What it does                                                                                                              |
| -------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `ci.yml`       | push to `main`, PRs    | `pytest` on Ubuntu + macOS × Python 3.9–3.14; separate `coverage` job uploads to Codecov                                  |
| `release.yml`  | push of `v*` tag       | Same tests → build wheel → publish to PyPI                                                                                |
| `artifact.yml` | after Release succeeds | Installs from PyPI, scaffolds real projects, cmake build + test, C library install + pkg-config/find_package verification |
| `docs.yml`     | push to `main`         | Builds MkDocs site and deploys to GitHub Pages                                                                            |

**CI must be green on `main` before tagging a release.**

______________________________________________________________________

## Contributing

**`main` is always working code.** Never commit directly to `main`.

### Branch workflow

1. Create a feature branch from `main`:

    ```sh
    git checkout main && git pull
    git checkout -b feat/my-feature   # or fix/, docs/, chore/
    ```

1. Make your changes. Commit early and often on the branch.

1. Push and open a PR:

    ```sh
    git push -u origin feat/my-feature
    gh pr create
    ```

1. Wait for CI to go green on the PR. Fix any failures before merging.

1. Add the PR to the **merge queue** ("Merge when ready"). The queue rebases it
    onto `main`, re-runs CI on the batched commit, and squash-merges it
    automatically when green — no manual rebase, even if other PRs land first.

1. The branch is deleted automatically on merge.

### Branch naming

| Prefix   | When to use                                |
| -------- | ------------------------------------------ |
| `feat/`  | New command, flag, or template feature     |
| `fix/`   | Bug fix                                    |
| `docs/`  | Docs-only changes                          |
| `chore/` | Deps, CI config, version bumps, formatting |

______________________________________________________________________

## Release checklist

See [release-checklist.md](release-checklist.md) for the step-by-step process.
