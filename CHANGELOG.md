# Changelog

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
