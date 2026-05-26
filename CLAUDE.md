# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Collaboration style

Be enthusiastic and energetic! Emojis are welcome and encouraged in
conversation, exploration, and discovery. Never put emojis in source files,
generated code, docstrings, comments, or any committed artifact — production
code stays clean.

## Commands

```sh
# Install just-makeit as an editable tool (do this once after checkout)
source .venv/bin/activate   # preferred over uv run
uv tool install .

# Install build deps (cmake, gcc/clang, numpy) into a venv
jm-install-deps /tmp/jm-venv

# Run all tests (CTest + pytest on the bundled examples)
jm-run-tests

# Run a single pytest file
pytest tests/test_templates.py

# Run a single test by name
pytest tests/test_templates.py::test_make_state_ctx_no_state

# Lint/format
uvx ruff format --line-length=79 src/ tests/
uvx ruff check src/ tests/

# Run a bundled end-to-end example (scaffolds fresh in /tmp, builds, tests)
just-makeit example fir_filter

# Build a wheel from the repo itself
just-makeit build
```

## Architecture

just-makeit is a **code-generation and build-orchestration tool**. It reads a
`just-makeit.toml` manifest in a user's project and generates C source,
CMakeLists.txt, Python bindings, type stubs, and tests — all wired together so
the user only writes the DSP algorithm.

### Source layout (`src/just_makeit/`)

| File | Role |
|---|---|
| `_cli.py` | Entry point; dispatches `just-makeit <cmd>` to submodules |
| `_types.py` | **Type system**: `_CTYPE_META` dict, all type-query helpers (`is_valid_type`, `array_elem_ctype`, etc.) |
| `_context.py` | **Context builders**: all `make_*_ctx()` functions that assemble render dicts |
| `_render.py` | **Render engine**: `render()`, 45 template constants loaded from files at import, `fn_c_stub/decl`, `render_module_ext_*` |
| `templates/` | **Template files**: `c/inc/`, `c/src/`, `cmake/`, `py/`, `make/`, `toml/`, `doc/`, `misc/` — 45 files; C/H use `/*<<token>>*/` placeholders for clang-format compatibility |
| `_config.py` | Read/write `just-makeit.toml`; all project state lives here |
| `_init.py` | `just-makeit new` / `just-makeit object` — standalone object scaffolding |
| `_object.py` | `just-makeit object` — builds render context and calls `_init.py` writers |
| `_module.py` | `just-makeit module` / `just-makeit object --module` — multi-object `.so` |
| `_method.py` | `just-makeit method` — adds named execute variants to an object |
| `_property.py` | `just-makeit property` — adds Python properties backed by getter/setter C fns |
| `_function.py` | `just-makeit function` — module-level C functions exposed to Python |
| `_add.py` | `just-makeit add` — appends state/init-param to existing object |
| `_perf.py` | `just-makeit perf` — idempotent retrofit of `JM_HOT`/`JM_FORCEINLINE` |
| `_impl.py` | `--impl file::funcname` and `--replace` — lifts a C function body from an existing file and splices it into generated stubs |
| `_script.py` | `just-makeit script` — reconstructs full CLI history from `just-makeit.toml` |
| `_build.py` | `just-makeit build/test/dry-run` — cmake configure + build + pytest |
| `_stubs.py` | Generates `.pyi` type stubs regenerated on every mutating command |
| `_scripts.py` | Entry points for `jm-install-deps`, `jm-run-tests`, `jm-docker-e2e` |
| `_example.py` | `just-makeit example` — runs bundled end-to-end walkthroughs |

### Template rendering

Templates live as real files in `src/just_makeit/templates/` and are loaded
at import time by `_render._load(relpath)`. Placeholders use `<<name>>`
syntax (not `{}` or `%s`) to avoid collisions with C/CMake brace syntax.
C/H templates use `/*<<token>>*/` so clang-format can parse them as valid C.
`render()` in `_render.py` tries the wrapped form first, then the bare form —
both are handled in a single pass.

Context dicts are assembled by chaining `make_*_ctx()` functions:
- `make_sample_ctx(arg_type, return_type)` — step() type metadata
- `make_state_ctx(component, Component, state_vars, ...)` — struct fields, constructor params, reset body
- `make_perf_ctx(perf)` — `JM_FORCEINLINE JM_HOT` vs `static inline`
- `make_step_ctx(ctx, arg_type, return_type, ...)` — full step()/steps() C and Python bodies
- `make_methods_ctx(...)` — extra named execute methods

### Generated project anatomy

A scaffolded project contains:
- `native/inc/<comp>/<comp>_core.h` — public C API + inline `step()`
- `native/src/<comp>/<comp>_core.c` — `steps()` + lifecycle (`create`/`destroy`/`reset`)
- `native/src/<comp>/<comp>_ext.c` — CPython extension glue (arg parsing, `PyMethodDef`, module init)
- `native/tests/test_<comp>_core.c` — CTest smoke test
- `src/<pkg>/<comp>.pyi` — type stub (regenerated on every mutation)
- `src/<pkg>/tests/test_<comp>.py` — pytest integration test
- `CMakeLists.txt` + `Makefile` — build system
- `just-makeit.toml` — single source of truth for all component metadata

### Module vs standalone objects

A **standalone object** gets its own `.so` (`my_project/engine.so`) imported
as `from my_project import Engine`.

A **module object** (`--module filter`) shares a `.so` subpackage
(`my_project/filter.so`) with other objects in the same module, imported as
`from my_project.filter import Fir, Biquad`. The module's `_ext.c` aggregates
all objects' `PyMethodDef` tables into a single `PyModuleDef`.

### `just-makeit.toml` schema

The TOML file is the project's persistent state — `_config.py` is the only
reader/writer. Key sections:

```toml
[project]
name = "my_project"
version = "0.1.0"
build = "cmake"          # or "make"
perf = "false"
pytest = "false"

[module.filter]           # multi-object module
objects = ["fir", "biquad"]

[engine]                  # per-component config
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

[[engine.state]]
name = "gain"
type = "double"
default = "1.0"

[[engine.methods]]        # extra named execute variants
name = "execute_ctrl"
arg_type = "float _Complex"
return_type = "size_t"
variable_output = true
```

### `JM_DEFINE_STEPS` macro

The perf path uses a C macro defined in the generated `jm_perf.h` header.
`JM_DEFINE_STEPS(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)` stamps out the
`fn_steps()` dispatch loop that selects between SIMD batch processing
(`fn_step_batch()`) and scalar fallback (`fn_step()`). User code only provides
`fn_step()` and optionally `fn_step_batch()`. The macro template lives at `templates/c/inc/jm_perf.h`, loaded as
`JM_PERF_H` in `_render.py`.

### `--impl` lifting

`--impl path/to/file.c::funcname` extracts the body of `funcname` from an
existing C file and injects it into the generated `/* <<IMPLEMENT>> */`
placeholder in the scaffolded stub. `--replace old::new` applies string
substitutions before injection. The pipeline lives in `_impl.py`:
`parse_impl` → `extract_body` → `apply_replacements` → `inject_body_into_stub`
or `patch_function_body`.

### Type system

All supported C types are registered in `_CTYPE_META` in `_types.py`.
Each entry specifies: `kind` (float/int/complex), `fmt` (PyArg_ParseTuple
format char), `zero` (C zero literal), `py_type` (numpy dtype string),
`parse_type` (intermediate C type for arg parsing), and `to_py` (lambda
producing the PyObject* conversion expression). Array types append `[]` to any
scalar key; fixed-length state fields append `[N]`.

### Windows build

On Windows, the generated `Makefile` detects `OS=Windows_NT` and passes
`-G "MinGW Makefiles"` to cmake. MSVC is not supported (C99 `float _Complex`
is rejected); MinGW/gcc is required. `install-deps.ps1` installs MinGW via
Chocolatey and creates a `make.exe` alias for `mingw32-make.exe`.

### Docker / Codespaces

- `docker/Dockerfile.examples-linux` — builds from local source; installs all
  9 bundled examples; used as the GitHub Codespaces base image
  (`ghcr.io/just-buildit/jm-examples-linux:latest`)
- `docker/Dockerfile.examples-windows` — Windows Server Core + MinGW
- Images are rebuilt on push to `main` (paths: `docker/**`, `src/**`,
  `pyproject.toml`) and on every release tag via `docker.yml` called from
  `release.yml`
- `.devcontainer/devcontainer.json` — Codespaces config; login shell so
  `docker/motd.sh` fires automatically

### CI / release

- `ci.yml` — matrix (ubuntu/macos/windows × py3.11–3.14); runs
  `jm-install-deps` then `jm-run-tests`
- `release.yml` — tag `v*` → test matrix → build wheel → PyPI publish →
  GitHub Release (changelog extracted from `CHANGELOG.md`) → rebuild Docker
  images
- `artifact.yml` — standalone artifact build/test job
