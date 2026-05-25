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
│   ├── _new.py               # `new` command — project scaffold
│   ├── _object.py            # `object` command — add a type (standalone or in-module)
│   ├── _init.py              # internal: standalone object file generation
│   ├── _module.py            # `module` command — scaffold empty extension module
│   ├── _add.py               # `add` command — append state vars to existing object
│   ├── _perf.py              # `perf` command — add performance annotations
│   ├── _build.py             # `build`/`test`/`dry-run` commands
│   ├── _config.py            # just-makeit.toml read/write
│   ├── _scripts.py           # entry points for jm-install-deps and jm-docker-e2e
│   ├── _templates.py         # all file templates (single source of truth)
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
├── examples/                 # complete worked examples (shipped in repo)
│   ├── running_stats/        # introductory walkthrough
│   ├── fir_filter/           # perf annotations, array state
│   ├── sliding_correlator/   # JM_DEFINE_STEPS
│   ├── sliding_power/        # --return-type, simd
│   ├── dsp_toolkit/          # multi-object standalone workflow
│   └── filter_module/        # module + object workflow
├── scripts/
│   └── copy_examples.py      # copies example sources into docs/
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
            ├── add       → _add.run()
            ├── perf      → _perf.run()
            ├── build/test/dry-run → _build.*
            └── config    → _config.*
```

### Templates (`_templates.py`)

All generated file content lives in `_templates.py` as string constants with
`<<placeholder>>` tokens. `T.render(tmpl, ctx)` does a simple string
replacement. The context dict is built by `_make_component_ctx()`,
`make_state_ctx()`, `make_sample_ctx()`, etc.

**To change what generated files look like:** edit `_templates.py`.
The constants are well-named (`COMPONENT_CORE_H`, `CMAKE_LISTS_COMPONENT`,
`PYTEST_TEST`, etc.).

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
uv sync
```

Run the tests:

```sh
uv run pytest                          # fast unit + integration tests
uv run pytest tests/test_examples.py  # slow: cmake build + run (skipped by default)
uv run pytest --benchmark-only        # scaffold generation benchmarks
```

The default `pytest.ini_options` in `pyproject.toml` ignores `test_examples.py`
to keep the normal suite fast.

______________________________________________________________________

## Git workflow

All non-trivial changes — new features, bug fixes, refactors, docs — go through
a branch and a PR. Direct pushes to `main` are reserved for release mechanics
(version bump + CHANGELOG commit, see [release-checklist.md](release-checklist.md)).

### Branch naming

| Prefix | Use |
|---|---|
| `feat/` | new command, flag, or generated output |
| `fix/` | bug fix |
| `docs/` | documentation only |
| `chore/` | tooling, CI, deps, version bump |

```sh
git checkout -b feat/out-type-scalar-param
# ... make changes, commit ...
gh pr create --fill
```

### PR rules

- CI must be green before merging (`ci.yml` runs on every PR).
- Keep PRs focused — one logical change per PR makes bisect and revert easy.
- The PR title becomes the CHANGELOG entry; write it accordingly
  (`fix: jm apply drops extra_link_libs on regeneration`).

### Merging

Squash-merge PRs to keep `main` history linear. After merge, delete the branch.

### What goes directly on `main`

Only two things skip the PR process:

1. **Release bump** — `chore: bump to X.Y.Z` (pyproject.toml + CHANGELOG only).
2. **Hotfix** — a one-liner fix that is urgent and trivially correct (rare).

______________________________________________________________________

## Adding a new template / feature

1. Add or edit the template constant in `_templates.py`.
1. Update the context builder if new placeholder keys are needed.
1. Wire the new file into the relevant `run()` function (`_new.py`, `_object.py`, `_init.py`).
1. Add tests in `tests/test_new.py` or `tests/test_init.py`.
1. Update the relevant page in `docs/commands/` and any relevant workflow docs.

______________________________________________________________________

## CI overview

| Workflow       | Trigger                | What it does                                                                                                              |
| -------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `ci.yml`       | push to `main`, PRs    | `pytest` on Ubuntu + macOS × Python 3.11–3.14                                                                             |
| `release.yml`  | push of `v*` tag       | Same tests → build wheel → publish to PyPI                                                                                |
| `artifact.yml` | after Release succeeds | Installs from PyPI, scaffolds real projects, cmake build + test, C library install + pkg-config/find_package verification |
| `docs.yml`     | push to `main`         | Builds MkDocs site and deploys to GitHub Pages                                                                            |

**CI must be green on `main` before tagging a release.**

______________________________________________________________________

## Release checklist

See [release-checklist.md](release-checklist.md) for the step-by-step process.
