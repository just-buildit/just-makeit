# Changelog

## \[0.9.0\] — 2026-05-10

### Breaking

- `just-makeit init` removed.  Use `just-makeit object <name>` (standalone,
  own `.so`) or `just-makeit object <name> --module <mod>` (grouped into a
  module subpackage).
- `new --component name` renamed to `new --object name`.
- `add --component name` renamed to `add --object name`.

At the C level nothing changes — the generated `_core.h`, `_core.c`, OBJECT
library, and Python binding are identical.  Only the CLI surface is unified:
`object` is now the single command for adding any Python type, standalone or
in-module.

### Docs

- README, workflow, commands, pure, perf, customization, c-library, and all
  example docs updated to use `object`/`--object` throughout.
- Quickstart restructured: **Standalone object** and **Module subpackage** are
  now clearly labelled separate paths.
- Commands table split `object` into two rows: one for standalone (no
  `--module`), one for in-module (with `--module`).

______________________________________________________________________

## \[0.8.4\] — 2026-05-10

### Added

- `just-makeit new <project> --module <name>` — scaffold one or more empty
  extension modules in the same command as the project.  `--module` is
  repeatable: `--module osc --module env` scaffolds both modules at once.
  Equivalent to running `just-makeit module` separately for each name.

### Docs

- README: commands table and quickstart updated for `new --module`
- `docs/commands.md`: `--module` argument documented under `new`
- `docs/workflow.md`: Scenario 3 updated to use `new --module filter`

______________________________________________________________________

## \[0.8.3\] — 2026-05-10

### Fixed

- Generated `Makefile` `test` target now treats pytest exit code 5 ("no tests
  collected") as success rather than falling through to the `unittest discover`
  fallback.  Module-only projects (no standalone components) have no
  `src/<pkg>/tests/` directory, so the fallback always failed.

______________________________________________________________________

## \[0.8.2\] — 2026-05-10

### Fixed

- Test: `TestNewScaffoldOnly.test_no_component_files` updated to allow
  the `native/src/<project>_lib.c` stub introduced in 0.8.1 — checks for
  absence of component *directories* rather than the directory itself.

______________________________________________________________________

## \[0.8.1\] — 2026-05-10

### Fixed

- `just-makeit new` now generates `native/src/<project>_lib.c` (a version stub),
  and `CMakeLists.txt` references it instead of `""`.  The empty-string source was
  rejected by CMake on macOS with AppleClang 17 (`No SOURCES given to target`).
- `just-makeit object` now patches `target_sources(<pkg>_lib PRIVATE
  $<TARGET_OBJECTS:<comp>_core>)` into the root `CMakeLists.txt` alongside the
  existing `add_subdirectory` patch.  Previously, module-only projects built an
  empty `lib<pkg>.so`; now all object cores are wired in, enabling `cmake --install`,
  pkg-config, and CMake `find_package` for module-based projects.
- CI: `artifact.yml` PyPI propagation retry extended from 10 to 20 × 30 s (10 min);
  `artifact.yml` adds C library install + pkg-config/find_package consumer steps for
  the `filter_module` workflow.

______________________________________________________________________

## \[0.8.0\] — 2026-05-09

### Added

- **`just-makeit module <name>`** — scaffold a named Python extension
  module (subpackage `.so`) that groups multiple types.  Creates
  `native/src/<name>/<name>_ext.c`, `CMakeLists.txt`, and
  `src/<pkg>/<name>/__init__.py`; records `[module.<name>]` in
  `just-makeit.toml`.
- **`just-makeit object <name> [--module <name>]`** — add a Python type
  to an existing module.  Generates the full C library scaffold
  (`_core.h`, `_core.c`, test, bench, OBJECT-only `CMakeLists.txt`)
  then fully regenerates the module's `_ext.c`, `CMakeLists.txt`, and
  `__init__.py` from the complete object list.  `--module` is inferred
  when only one module exists.  Supports all flags from `init`:
  `--state`, `--pure`, `--arg-type`, `--return-type`, `--perf`.
- Module `_ext.c` is always regenerated from scratch — never patched —
  so adding a third type never disturbs existing ones.
- Types within a module may have different `--arg-type`/`--return-type`
  (e.g. `Fir` processes `float complex`, `Biquad` processes `float`).

### Fixed

- Generated C tests now use a `CHECK(cond)` macro counter instead of
  `assert()`.  Failures print `FAIL file:line expr` and exit nonzero —
  no silent pass under `-DNDEBUG`.
- Generated `CMakeLists.txt`: test and bench targets now link `-lm`,
  preventing linker failures on projects that use `<math.h>`.

### Docs

- `docs/commands.md`: `module` and `object` command reference added.
- `examples/filter_module/`: complete walkthrough — `Fir` (complex) +
  `Biquad` (real) in a single `filter` module, with end-to-end
  `test.py` covering scaffold, patch, build, ctest, and spectral checks.
- `artifact.yml`: filter_module scaffold + verify + build + smoke test
  block added to the release artifact CI.

______________________________________________________________________

## \[0.7.0\] — 2026-05-09

### Added

- Generated project `README.md` now includes a Requirements section
  listing Python 3.11+, CMake ≥ 3.16, a C99 compiler, NumPy, and
  pkg-config with per-platform install commands.
- `docs/c-library.md` — dedicated end-user guide for installing and
  consuming the generated C library: prerequisites, build + install,
  pkg-config and CMake find\_package usage, rpath options, and
  verification steps.

### Fixed

- `just-makeit init` now uses `target_sources(… PRIVATE $<TARGET_OBJECTS:…>)`
  to wire component OBJECT libraries into the combined shared library.
  The previous `target_link_libraries` approach produced an empty
  `lib<project>.so` on some CMake versions.
- Generated `.pc` file now has the correct `includedir` when installing
  to a non-default prefix: cmake is reconfigured with
  `CMAKE_INSTALL_PREFIX` before `cmake --install`, ensuring
  `configure_file` regenerates the `.pc` with the right paths.

### Docs

- `docs/workflow.md`: C library section updated with correct cmake
  install sequence, split pkg-config invocation, `--as-needed` note,
  and link to `docs/c-library.md`.

______________________________________________________________________

## \[0.6.9\] — 2026-05-09

### Changed

- CI: `artifact.yml` C library section consolidated from five steps to
  two: **Install C library** (reconfigure + install) and **Verify C
  consumers** (pkg-config + CMake find_package in a single step).

______________________________________________________________________

## \[0.6.8\] — 2026-05-09

### Fixed

- CI: `artifact.yml` pkg-config consumer now splits `--cflags` and
  `--libs` with the source file between them.  Ubuntu's `--as-needed`
  linker default silently drops a shared library that appears before
  the object referencing it, causing undefined-reference errors.

______________________________________________________________________

## \[0.6.7\] — 2026-05-09

### Fixed

- Generated `libmy_project.so` now actually contains the component
  symbols.  `just-makeit init` was appending
  `target_link_libraries(…_lib PRIVATE …_core)` to link OBJECT files
  into the combined shared library, which is unreliable across CMake
  versions and produces an empty `.so`.  Replaced with
  `target_sources(…_lib PRIVATE $<TARGET_OBJECTS:…_core>)`, the
  canonical approach supported since CMake 3.1.

______________________________________________________________________

## \[0.6.6\] — 2026-05-09

### Fixed

- CI: `artifact.yml` C library install now reconfigures cmake with the
  target prefix (`-DCMAKE_INSTALL_PREFIX=...`) before `cmake --install`,
  so the generated `.pc` file has the correct `includedir` rather than
  the default `/usr/local` baked in at build time.

______________________________________________________________________

## \[0.6.5\] — 2026-05-09

### Fixed

- CI: `artifact.yml` pkg-config step now `export`s `PKG_CONFIG_PATH`
  before the `$(pkg-config ...)` subshell expansion; the previous
  inline prefix only applied to `gcc`, not to the subshell.

______________________________________________________________________

## \[0.6.4\] — 2026-05-09

### Changed

- CI: `artifact.yml` PyPI propagation wait replaced with a retry loop
  (10 × 30 s, up to 5 min) so the smoke test never fails on slow PyPI
  indexing.

### Docs

- `docs/roadmap.md`: v0.6.2 and v0.6.3 shipped sections added;
  `just-makeit ci --provider github|woodpecker` added to ideas.
- `README.md`: `dsp_toolkit` description updated to reflect
  `__init__.py` auto-splice (gap is fixed, not demonstrated).

______________________________________________________________________

## \[0.6.3\] — 2026-05-09

### Changed

- CI: `artifact.yml` rewritten around `fir_filter` example — real algorithm,
  array + complex state, `just-makeit perf`, impulse response assertion in
  the C consumer, multi-component `__init__.py` splice check.

______________________________________________________________________

## \[0.6.2\] — 2026-05-09

### Changed

- CI: post-publish smoke tests extracted into dedicated `artifact.yml`
  (triggered via `workflow_run` on Release); `release.yml` now handles
  `test → build → publish` only.

______________________________________________________________________

## \[0.6.1\] — 2026-05-09

### Added

- `examples/dsp_toolkit` — two-component library (Gain + EMA) that walks
  through the full multi-component workflow end-to-end and verifies the
  `__init__.py` auto-splice in CI.
- `docs/workflow.md` rewritten around two end-to-end scenarios: standalone
  extension and multi-component package.

### Fixed

- `just-makeit init` now automatically splices the new component's import and
  `__all__` entry into the existing `src/<pkg>/__init__.py` instead of leaving
  it untouched.  Handles missing `__all__`, multi-line `__all__`, and user
  additions; idempotent.
- Generated `pyproject.toml` lists `pytest-benchmark` as a runtime dependency
  (moved from `[project.optional-dependencies]`) so `pip install .` provides
  everything needed to run `make bench`.
- `JM_UNROLL` comment in `jm_perf.h` corrected: it is a directive (obeyed
  unconditionally), not an advisory hint like `JM_HOT` or `JM_LIKELY`.

______________________________________________________________________

## \[0.6.0\] — 2026-05-08

### Added

- `--arg-type TYPE` and `--return-type TYPE` flags on `just-makeit new` and
  `just-makeit init` — generated `step()` and `fn()` signatures are no longer
  hardcoded to `float _Complex`.  Both flags accept any supported C scalar type:
  `float`, `double`, `float _Complex`, `double _Complex`.
- Generated Python bindings, `.pyi` stubs, C tests, benchmarks, and NumPy
  `steps()` loops all derive their types from the declared `arg_type` /
  `return_type` — no manual edits needed after scaffolding.
- `arg_type` and `return_type` fields persisted in `just-makeit.toml`; read
  back by `just-makeit add` so regenerated files stay consistent.
- `make_sample_ctx(arg_type, return_type)` in `_templates.py` — single source
  of truth for all type-derived template keys (`<<arg_ctype>>`, `<<return_ctype>>`,
  `<<in_np_enum>>`, `<<out_np_dtype>>`, `<<step_parse_block>>`, etc.).
- `examples/sliding_power` — end-to-end example using `--return-type float`
  since signal power is real-valued; demonstrates that `step()` need not return
  the same type it receives.

______________________________________________________________________

## \[0.5.0\] — 2026-05-08

### Added

- `jm_simd.h` — width-portable SIMD operation macros included automatically
  with `--perf`.  Provides `JM_VEC_F32`/`JM_VEC_F64` types and `JM_ZERO_`,
  `JM_SPLAT_`, `JM_LOAD_`, `JM_STORE_`, `JM_ADD_`, `JM_MUL_`, `JM_FMA_`,
  `JM_MAC_`, `JM_HSUM_` macro families, plus `jm_dot_f32`/`jm_dot_f64` helpers.
  ISA tier selected at compile time: AVX-512F → AVX2+FMA → scalar fallback.
  Write `step_batch()` once; the compiler picks the best vector width.
- New macros added to `jm_perf.h`: `JM_UNROLL(n)`, `JM_ASSUME_ALIGNED(ptr, n)`,
  `JM_PREFETCH(ptr, rw, loc)` — loop unroll hint, alignment assertion for
  auto-vectorisation, and software prefetch.

______________________________________________________________________

## \[0.4.0\] — 2026-05-08

### Added

- **C library distribution** — each component's `_core.c` now compiles as a
  CMake OBJECT library (`<comp>_core` OBJECT) and links into *both* the Python
  DSO and a combined `lib<project>.so`.  One compilation, two consumers.
- `lib<project>.so` target in the top-level `CMakeLists.txt`, accumulating all
  component OBJECT targets.  `just-makeit init` patches
  `target_sources(${PROJECT_NAME}_lib …)` alongside the existing
  `add_subdirectory` patch.
- `cmake/<project>.pc.in` — pkg-config template; `cmake --install` makes
  `gcc $(pkg-config --cflags --libs my-project) main.c` work out of the box.
- `cmake/<project>-config.cmake.in` — CMake `find_package` template for C/C++
  consumers; exposes `my_project::my_project` imported target.
- `native/inc/<project>.h` — umbrella header that `#include`s all component
  headers; the installed library exposes exactly one include path.
- `install()` rules for the shared library, all headers, pkg-config file, and
  CMake config package.

______________________________________________________________________

## \[0.3.0\] — 2026-05-08

### Added

- `--pure` flag on `just-makeit new` and `just-makeit init` — generates a
  **stateless** component where the caller supplies all parameters per call.
  Style is auto-detected from the state variable types:
  - **Scalar-only state** → *scalar* style: params passed per call as function
    arguments.  The Python module exports `<comp>(x, **params)` and
    `<comp>_steps(arr, **params)`; a `.steps` attribute is attached to the
    function in `__init__.py` so `<comp>.steps(arr)` also works.
  - **Any array state** → *struct* style: caller-managed `<comp>_params_t`
    struct with heap-alloc helper `_params_create()` (uses `calloc`; comment
    shows `aligned_alloc` for SIMD), `_params_free()`, and `_params_init()` for
    stack/custom allocation.  Python exposes a `<Component>` callable class
    (`obj(x)` via `tp_call`, `obj.steps(arr)`, context-manager support).
- Both pure styles ship with: C test, C benchmark, Python benchmark, `.pyi`
  stub, and pytest test — all regenerated on `just-makeit add --state`.
- `examples/fir_filter` Step 8: pure FIR variant demonstrating struct-style
  caller-managed params, multiple independent channels, and stack allocation.
- `pure` field persisted in `just-makeit.toml`; read back by `just-makeit add`
  to select the correct template set when state variables change.

## \[0.2.0\] — 2026-05-08

### Added

- `just-makeit perf` command — upgrades an existing project to use performance
  annotations in-place: writes `jm_perf.h`, patches `step()` with
  `JM_FORCEINLINE JM_HOT`, records `perf = true` in `just-makeit.toml`.
  Never touches user-written function bodies.  Idempotent.
- `JM_DEFINE_STEPS` macro in `jm_perf.h` — stamps out `<fn>_steps()` from
  three clearly separated concerns: `LENGTH` (history depth, algorithm),
  `BATCH` (SIMD width, parallelism), `CHUNK` (scratch-buffer fill, tuning).
  Eliminates hand-written outer dispatch loops.
- `sliding_correlator` example — demonstrates `JM_DEFINE_STEPS` is
  algorithm-agnostic using complex cross-correlation (`Σ conj(ref[k])·x[n-k]`).
  `step_batch()` is a compiler-vectorizable scalar loop; no explicit SIMD
  intrinsics required.
- `docs/perf.md` — reference guide covering `just-makeit perf`, all
  `jm_perf.h` macros, and `JM_DEFINE_STEPS` with generic + FIR examples.

### Changed

- `examples/fir_filter` Step 7: reworked to use `just-makeit perf` on the
  existing `my_fir` project instead of scaffolding a new `my_fir_perf` copy.
  The `step()` implementation from Step 2 is preserved — no copy-paste.
- `JM_DEFINE_STEPS` parameter renamed `taps` → `LENGTH` to reflect its
  generic meaning (history depth), separating it from the FIR-specific `TAPS`
  concept.  FIR example now defines `FIR_TAPS = 16` and
  `FIR_LENGTH = FIR_TAPS - 1`.

______________________________________________________________________

## \[0.1.2\] — 2026-05-06

### Fixed

- `--basic` Makefile: numpy include path now resolved as a shell subcommand at recipe execution time, fixing `numpy/arrayobject.h: No such file or directory` when numpy is installed during the build
- `__version__` now derived from installed package metadata instead of a hardcoded string

### Docs

- `docs/index.md`: corrected stale `just-makeit init` references to `just-makeit new`

______________________________________________________________________

## \[0.1.1\] — 2026-05-06

### Added

- `--basic` flag on `just-makeit new` — plain `cc` + `sysconfig` build with no CMake or build directory; stored as `build = "make"` in `just-makeit.toml`; `just-makeit init` patches the Makefile automatically for additional components

### Fixed

- `--basic` Makefile: numpy auto-installed via pip before compile; `NP_INC`/`INC` use lazy `=` so the include path is resolved after installation
- `just-makeit new` done hint now correctly includes `cd <project> &&` prefix
- `make test` falls back to `python -m unittest discover` when pytest is not installed
- Generated `__init__.py` no longer contains an unrunnable doctest
- `examples/gain/`: `#pragma once` replaced with C99 include guards; Makefile synced with template fixes
- PyPI README: `examples/gain/` link changed to absolute GitHub URL

______________________________________________________________________

## \[0.1.0\] — 2026-05-05

### Added

- `just-makeit new <project>` — scaffold a complete project (CMakeLists.txt, Makefile, pyproject.toml, README, .gitignore, common headers)
- `just-makeit init <component>` — add a C extension component to an existing project
- `just-makeit add --state` — add state variables to an existing component
- `just-makeit build` — configure + build C, then package a wheel via just-buildit
- `just-makeit config` — show or edit project configuration
- Multi-component project support: each component gets its own `native/src/<comp>/CMakeLists.txt`; `just-makeit init` appends `add_subdirectory` to the top-level CMakeLists
- Generated code: C99 lifecycle pattern (create / step / steps / reset / destroy), getter/setter pairs, NumPy `steps()` binding
- pytest + CTest test generation covering create, step, steps, getters/setters, reset, context manager, and destroy
- pytest → `unittest discover` fallback in generated Makefile (no pytest required)
- numpy auto-install in generated Makefile (`pip install numpy` if missing before cmake)
- just-buildit PEP 517 backend wired in generated `pyproject.toml`
- C99 include guards throughout (no `#pragma once`)
