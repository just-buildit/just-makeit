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

# Lint/format — ALWAYS via make; never `uvx ruff` or a global install.
# `uvx` resolves to whatever ruff released today, which formats differently
# from the pinned one and silently rewrites unrelated files.
make format   # auto-fix (ruff + mdformat)
make lint     # the gate CI runs (all pre-commit hooks, all files)

# What targets exist is answered by make, never by a hand-written list
make help

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

| File                 | Role                                                                                                                                                                             |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_cli.py`            | Entry point; dispatches `just-makeit <cmd>` to submodules                                                                                                                        |
| `_cli_*.py`          | Per-command argument parsers (`_cli_new`, `_cli_object`, `_cli_method`, `_cli_function`, `_cli_remove`, `_cli_parse`)                                                            |
| `_color.py`          | ANSI color helpers; respects `NO_COLOR` and non-TTY fallback                                                                                                                     |
| `_types.py`          | **Type system**: `_CTYPE_META` dict, all type-query helpers (`is_valid_type`, `array_elem_ctype`, etc.)                                                                          |
| `_context/`          | **Context builders** (package), all `make_*_ctx()`: `_sample`, `_state`, `_step`, `_methods`, `_types`, `_parse`, `_destroy`, `_diagnostics`, `_modpath`, `_platform`, `_stream` |
| `_render.py`         | **Render engine**: `render()`, template constants loaded from files at import, `fn_c_stub/decl`, `render_module_ext_*`                                                           |
| `templates/`         | **Template files**: `c/inc/`, `c/src/`, `cmake/`, `py/`, `make/`, `toml/`, `doc/`, `misc/`; C/H use `/*<<token>>*/` placeholders for clang-format compatibility                  |
| `_config.py`         | Read/write `just-makeit.toml`; all project state lives here                                                                                                                      |
| `_new.py`            | `just-makeit new` — creates a new empty project scaffold                                                                                                                         |
| `_init.py`           | Low-level file writers shared by `object`, `module`, and `new`                                                                                                                   |
| `_object.py`         | `just-makeit object` — builds render context and calls `_init.py` writers                                                                                                        |
| `_module.py`         | `just-makeit module` / `just-makeit object --module` — multi-object `.so`                                                                                                        |
| `_method.py`         | `just-makeit method` — adds named execute variants to an object                                                                                                                  |
| `_property.py`       | `just-makeit property` — adds Python properties backed by getter/setter C fns                                                                                                    |
| `_function.py`       | `just-makeit function` — module-level C functions exposed to Python                                                                                                              |
| `_add.py`            | `just-makeit add` — appends state/init-param to existing object                                                                                                                  |
| `_perf.py`           | `just-makeit perf` — idempotent retrofit of `JM_HOT`/`JM_FORCEINLINE`                                                                                                            |
| `_impl.py`           | `--impl file::funcname` and `--replace` — lifts a C function body from an existing file and splices it into generated stubs                                                      |
| `_apply.py`          | `just-makeit apply` — additive replay: materializes any files missing from the project manifest                                                                                  |
| `_remove.py`         | `just-makeit remove` — deletes generated files and strips TOML/CMake wiring                                                                                                      |
| `_app.py`            | `just-makeit app` — scaffolds a C executable, Python console script, or PEP 723 inline script from a component                                                                   |
| `_bench.py`          | `just-makeit bench` — builds and runs C + Python benchmarks; saves dated snapshots                                                                                               |
| `_upgrade.py`        | `just-makeit upgrade` — schema migration for existing projects                                                                                                                   |
| `_split_objects.py`  | `just-makeit split-objects` — moves per-object TOML sections into `objects/*.toml` fragments                                                                                     |
| `_script.py`         | `just-makeit script` — reconstructs full CLI history from `just-makeit.toml`                                                                                                     |
| `_build.py`          | `just-makeit build/test/dry-run` — cmake configure + build + pytest                                                                                                              |
| `_stubs.py`          | Generates `.pyi` type stubs regenerated on every mutating command                                                                                                                |
| `_scripts.py`        | Entry points for `jm-install-deps`, `jm-run-tests`, `jm-docker-e2e`                                                                                                              |
| `_example.py`        | `just-makeit example` — runs bundled end-to-end walkthroughs                                                                                                                     |
| `_bind.py`           | `just-makeit bind` — derives a manifest from an existing C header                                                                                                                |
| `_ci.py`             | `just-makeit ci` — scaffolds/refreshes the generated project's CI                                                                                                                |
| `_error.py`          | `just-makeit error` — declares a `create()`-failure exception (gh-482)                                                                                                           |
| `_warning.py`        | `just-makeit warning` — post-construction `PyErr_WarnEx` block (gh-481)                                                                                                          |
| `_status.py`         | `just-makeit status` / `--check` — reports manifest-vs-tree drift                                                                                                                |
| `_createonly.py`     | Which create-only files can be *behind* (`OUTDATED`) and which merely differ from their scaffold                                                                                 |
| `_regenerate.py`     | `just-makeit regenerate <component>` — rebuilds one component's glue                                                                                                             |
| `_migrate.py`        | `just-makeit migrate-to-fragments` — moves `[obj]`/`[module.X]` out of the central manifest into `objects/*.toml` + `modules/*.toml` (TOML only; it never touches C)             |
| `_view.py`           | `just-makeit view` — a second Python class over one C core (gh-504)                                                                                                              |
| `_cli_view.py`       | CLI handler for `just-makeit view`                                                                                                                                               |
| `_glue.py`           | Regenerates a component's Python glue from the manifest                                                                                                                          |
| `_handle.py`         | Code generator for `kind = "handle"` modules (gh-306)                                                                                                                            |
| `_capsule.py`        | Code generator for `kind = "capsule"` modules (gh-286)                                                                                                                           |
| `_composer.py`       | Code generator for `kind = "composer"` modules (gh-287)                                                                                                                          |
| `_codec.py`          | Declarative variant codecs — SSOT for discriminant-tagged binary values                                                                                                          |
| `_record.py`         | One shape for a single-record result, shared by every face (gh-646)                                                                                                              |
| `_coerce.py`         | Shared argument-coercion primitives for generated CPython glue                                                                                                                   |
| `_keys.py`           | Recognises the keys a manifest table may carry, and says so when it does not                                                                                                     |
| `_docstring.py`      | **Doc engine**: derives Python docstrings from C header Doxygen; `render_numpy_doc` / `render_runtime_doc` share one section builder                                             |
| `_gluedoc.py`        | Docstrings for the methods jm generates for its own machinery (`destroy`, `__enter__`/`__exit__`, the serializable triplet)                                                      |
| `_docsync.py`        | Refreshes runtime `__doc__` in per-object binding fragments on `apply`                                                                                                           |
| `_report.py`         | One place that decides how heavily a warning reads                                                                                                                               |
| `_cfmt.py`           | Optional house-style pass over generated C (gh-265)                                                                                                                              |
| `_pyfmt.py`          | Holds generated Python to the project's column target                                                                                                                            |
| `_fmtprobe.py`       | Asks whether a formatter command means the same thing from any directory                                                                                                         |
| `_codecheck.py`      | Reports authored `@code` lines too wide for their stub                                                                                                                           |
| `_hollow.py`         | Detects targets that pass without covering anything (gh-806)                                                                                                                     |
| `_libwiring.py`      | Which component cores reach `lib<pkg>.so` / `.a` — emitter and detector in one file (gh-981/gh-984)                                                                              |
| `_termynal_fence.py` | Superfences formatter for animated terminal (termynal) docs blocks                                                                                                               |

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

A module id may be **dotted** (`--module dsp.filters`) to nest it in a
subpackage: `from my_project.dsp.filters import Fir`. `C.module_paths(id)`
(a `ModulePaths` NamedTuple) splits the name's three roles — `leaf` (`filters`:
`PyInit_`/`.m_name`/`.so` basename via CMake `OUTPUT_NAME`), `cname`
(`dsp_filters`: the unique CMake target + flat `native/src/<cname>/` dir, so
the `add_subdirectory` regex stays a single `\w+`), and `pypath` (`dsp/filters`:
the Python output dir). Intermediate packages get a plain `__init__.py`
(`_init.ensure_parent_packages`). `Ctx.make_module_ctx` supplies the render
slots (`module`=cname, `module_leaf`, `module_pypath`, `module_output_name`,
`module_tp`); they collapse to today's values for a flat id (zero churn). The
TOML key is quoted (`[module."dsp.filters"]`); the split-layout fragment file is
`modules/<cname>.toml`. `_apply._splice_cmake_components` classifies module
blocks by `C.module_cnames(cfg)` (not the dotted id).

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
step_delegates_to_steps = "true"  # step() delegates to steps() (gh-208; only when set)
streamable = "true"       # generate stream()/__iter__ (only when set)
stream_block_default = "1024"  # __iter__ / stream() default block

[[engine.state]]
name = "gain"
type = "double"
default = "1.0"

[[engine.methods]]        # extra named execute variants
name = "execute_ctrl"
arg_type = "float _Complex"
return_type = "size_t"
variable_output = true

[[engine.methods]]        # gh-788: rows of a C struct as a structured ndarray
name = "read"
variable_output = true
record_dtype = "dp_tlm_rec_t"   # the POD struct; the author declares it
[[engine.methods.result_fields]]   # ...and these are the dtype's columns
name = "n"
type = "uint64_t"
```

### Record shapes: `single` vs `record_dtype`

Both carry `result_fields`, and they are different results:

| key            | returns                                   | C kernel writes                   |
| -------------- | ----------------------------------------- | --------------------------------- |
| `single`       | ONE record, a named `PyStructSequence`    | the struct by value               |
| `record_dtype` | an ARRAY of records, a structured ndarray | `<struct> *out`                   |
| neither        | a `list[tuple]`                           | `<T> *result, size_t max_results` |

The CLI rejects `single` and `record_dtype` together. `record_dtype` is the
element type, so it reaches the `*out` parameter, the data-pointer cast and
the `.pyi` through the one slot `out_type` already uses — but **four** places
must suppress the list-of-records reading of `result_fields`:
`_context/_methods.make_methods_ctx`'s declaration chain, its return-annotation
chain, `_method._build_method_prototype`, and `_method.run`'s stub dispatch.
`_apply` and `_script` forward the key explicitly (they enumerate method keys
one by one, so an unnamed key is silently absent — that dropped the shape and
made `apply` rewrite the sacred header prototype).

The numpy dtype is built at **runtime by the generated C**, from `offsetof` and
`sizeof`, because jm never sees the struct definition — it is the author's, in
the sacred header. `_record.dtype_c` emits it; `_record.find_dtype` is what
lets the gh-729 incremental splice carry the cache and its builder along with
the wrapper that calls them.

### The capsule triangle

A foreign C pointer crosses the Python boundary as a named `PyCapsule`. Every
direction shares an emitter in `_context/_parse.py` — `capsule_new_c` for
producing, `capsule_unwrap_c` for consuming. Two copies of a name-checked
`PyCapsule_GetPointer` plus its duck-typed `._capsule` fallback is exactly the
pair that drifts, and the producing side's `NULL` destructor is a **contract**,
not a call.

| direction         | declared as                                  | issue        |
| ----------------- | -------------------------------------------- | ------------ |
| consume, per call | a method `param` with `capsule`              | gh-432       |
| produce (object)  | a property `--type capsule --capsule <name>` | gh-788 gap 4 |
| produce (handle)  | `capsule = "<name>"` on a `kind = "handle"`  | gh-794       |
| construct         | an `init_param` with `capsule`               | gh-790       |

`capsule_new_c` takes no destructor argument on purpose: a capsule that *owns*
its pointer is a different feature and should look different. Both producers
keep their liveness guard (destroyed / closed) before handing the pointer out —
a capsule carries no liveness for the consumer to check.

The emitter has **three** knobs, and the third exists because two of them were
briefly one:

- `fail` — `return NULL;` in a wrapper vs `return -1;` in an `initproc`. A
    hard-coded `return NULL` inside an `initproc` compiles and reports *success*.
- `allow_none` — `None` maps to `NULL` (the detach idiom, and gh-805 §H's
    "no time base stated") vs the handle is mandatory and `None` is rejected up
    front.
- `explain_type_error` — replace the raw `AttributeError` from the `._capsule`
    lookup with a `TypeError` naming what to pass. True for a **constructor**.

`explain_type_error` was `allow_none` until gh-805 §H. They were the same
question only by accident: every constructor param was mandatory, so
`allow_none=False` could stand in for "this is a `tp_init`". A nullable
constructor param breaks that coupling, and leaving them fused silently
downgraded the message to `'int' object has no attribute '_capsule'` on exactly
the call the upgrade was written for. A flag standing in for a second question
is a bug waiting for the first case that separates them.

Constructing adds two things nothing else needs:

- a **strong reference to the Python owner**, in a dedicated
    `capsule_owner_fields` / `capsule_owner_free` template slot. Deliberately not
    `extra_buf_fields`: `make_methods_ctx` runs after `make_state_ctx` and
    replaces that slot wholesale, so an owner field put there vanishes the moment
    the object also declares a method.
- the param's `header`, which must reach the **sacred `_core.h`** because the
    foreign type is in the `create()` prototype. `C.param_headers` reads the
    manifest, but at object-creation the component is not in it yet — so
    `_init._param_headers_at_create` also reads the `init_params` argument.

`_CTOR_OVERRIDE_KEYS` in `_state.py` is an explicit **allow-list**: a slot
missing from it is silently dropped, not left unreplaced.

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
producing the PyObject\* conversion expression). Array types append `[]` to any
scalar key; fixed-length state fields append `[N]`.

### Windows (opt-in, gh-213)

jm itself does not test Windows — it emits CPython for MinGW/GCC, MSVC was
never exercised, and the Windows CI was dropped. Windows support in generated
projects is **opt-in per project** via `[project] platforms` (default
`["linux", "macos"]`). `jm new --windows` records `platforms = [..., "windows"]`
and emits the MinGW runtime-DLL `if(WIN32 …)` block into each component/module
`CMakeLists.txt` (gated by `Ctx.make_platform_ctx` / `C.is_windows_target`); off
by default that block is absent and `jm status --check` treats the absence as
correct. The generated `Makefile` still detects `OS=Windows_NT` at make-time
(MinGW required; MSVC rejects C99 `float _Complex`).

### Docker / Codespaces

- `docker/Dockerfile.examples-linux` — builds from local source; installs all
    bundled examples; used as the GitHub Codespaces base image
    (`ghcr.io/just-buildit/jm-examples-linux:latest`)
- Images are rebuilt on push to `main` (paths: `docker/**`, `src/**`,
    `pyproject.toml`) and on every release tag via `docker.yml` called from
    `release.yml`
- `.devcontainer/devcontainer.json` — Codespaces config. `remoteUser` must be
    the image's own `USER`, not `root`: the image's home **is** the workspace
    folder, so as root every `~/...` path the sandbox prints resolves under
    `/root` and does not exist. Gated by
    `tests/test_devcontainer_and_welcome.py`.
- The sandbox's welcome text has one source and is **generated**, never
    written: `docker/build_examples.py` records what it actually built to
    `.jm-built.json`, `docker/welcome.py` renders that into
    `$JM_HOME/README.md`, the editor opens it (`workbench.startupEditor`) and
    `docker/motd.sh` prints the same file. The hand-written version advertised
    a `my_corr/` no example produces and cited a
    `just_makeit._example_readme` module that has never existed.

### Makefile standard (`standard.mk`)

The shared make targets are **not** written here. `standard.mk` is vendored
from the cross-org standard (canonical:
`https://just-buildit.github.io/standard.mk`, published by P1) and the
repo's `Makefile` is configuration only — feature flags, command variables,
`include standard.mk`. Plan and success criteria live in the
just-buildit/.github README under "Makefile standard".

- **Never edit `standard.mk` in place.** It is vendored verbatim; the
    `standard-check` drift gate fails `make lint` on any difference from
    canonical. Per-repo variation is a variable in `Makefile`; a shared change
    goes to canonical and comes back through the vendored copy.

- `HAS_PYTHON/DOCS/BENCH/RELEASE/EXAMPLES` are on here. `HAS_C` is off — jm
    generates C but builds none itself, so `build` would have nothing to build;
    the C toolchain is exercised via `test-examples`.

- Three gates hang off `lint`, so CI (which runs `make lint` and nothing else)
    enforces them: `standard-check` (drift), `help-check` (every target
    documented, every rule listed), `ghost-check` (no `.PHONY` entry without a
    recipe or prerequisites). `tests/test_lint_ssot.py` guards the wiring.

- `help` is generated from the `## description` on each rule; a hand-written
    target list is what let `make wheel` stay advertised in doppler after its
    rule was gone.

- **The drift gate is live.** `STANDARD_URL` defaults to canonical inside
    `standard.mk`, so vendoring the file is what arms it — there is no per-repo
    line to forget. `make lint` therefore needs network; that is deliberate, as
    a gate that cannot reach its reference has not passed. A deliberate opt-out
    is `STANDARD_URL =` in the Makefile.

- A genuinely repo-local target goes in `local.mk` and is named in
    `LOCAL_TARGETS` (so `help` and the gates see it). **Which ones jm has is
    answered by `make help`'s *Local* section, never by a list here** — this
    said "exactly one" while `LOCAL_TARGETS` held five, the same shape as the
    version and schema literals below.

    `examples-clean` is the one worth explaining rather than counting: it is
    deliberately NOT in `HAS_EXAMPLES` — doppler has examples too but cleans
    them from its own `clean`, so a required `EXAMPLES_CLEAN_CMD` would force
    it to invent a command for a target it does not want. Criterion 10
    requires shared targets to be *in* the standard; it does not license the
    converse.

### CI / release

- `ci.yml` — matrix (ubuntu/macos/ubuntu-arm64 × py3.9–3.14); runs
    `jm-install-deps` then `jm-run-tests`. No Windows leg (jm itself isn't
    tested there — see the Windows section above).
- `release.yml` — tag `v*` → test matrix → build wheel → PyPI publish →
    GitHub Release (changelog extracted from `CHANGELOG.md`) → rebuild Docker
    images
- `artifact.yml` — standalone artifact build/test job
