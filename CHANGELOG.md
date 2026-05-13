# Changelog

## \[0.10.9\] — 2026-05-13

### Fixed

- **`array_processing` example — leftover `my_conv` directory**: the example
  scaffolds `my_conv` for a structural assertion then deletes it, so it no
  longer appears as an unbuilt project in the Docker examples directory and
  fails the `.pyd`-presence smoke test.

## \[0.10.8\] — 2026-05-13

### Fixed

- **Windows — generated Makefile `SHELL`**: switched from `SHELL = sh.exe` to
  `SHELL = cmd.exe` on `Windows_NT`.  MinGW's `sh.exe` is present in the
  distribution but its MSYS2 DLL dependencies are not on `PATH` inside a
  Windows container, so invoking it raised "The system cannot find the path
  specified."  The `test` target and the dependency-check lines in
  `$(BUILD_DIR)/CMakeCache.txt` are now written with OS-specific `ifeq` blocks:
  Windows uses `2>nul` redirects and a Python one-liner to handle pytest exit
  code 5 (no tests collected); non-Windows keeps the original POSIX shell forms.
  `NPROC` and `PYTHON` variable defaults also have Windows-specific branches
  (`NPROC ?= 4`; `python` instead of `python3`).

## \[0.10.7\] — 2026-05-13

### Fixed

- **Generated pytest — void-input generators**: `test_step_runs`,
  `test_steps_shape_dtype`, `test_context_manager`, and `test_destroy` now
  emit `obj.step()` (no argument) and `obj.steps(64)` (integer count) for
  objects scaffolded with `--arg-type void`.  Previously all four tests
  passed a value to `step()` and an ndarray to `steps()`, which caused
  `TypeError` at runtime for generator objects.

## \[0.10.6\] — 2026-05-13

### Fixed

- **Windows — generated Makefile `SHELL`**: both Makefile templates now use
  `SHELL = sh.exe` on `Windows_NT` instead of `SHELL = /bin/sh`, so
  `mingw32-make` uses MinGW's bundled `sh.exe` rather than falling back to
  `cmd.exe`.  Without this, the `test` target's `ret=$$?; [ $$ret -eq 0 ]`
  syntax failed with `'[' is not recognized as an internal or external command`.

## \[0.10.5\] — 2026-05-12

### Fixed

- **Docker / rootless containers**: `jm-install-deps` and `just-makeit
  install-deps` no longer call `sudo` when running as root (e.g. inside a
  Docker `RUN` step). Previously, `install-deps.sh` hardcoded `sudo` in every
  package-manager invocation; it now omits `sudo` when `id -u` returns 0.
- **Windows — `.pyd` import after `pip install -e .`**: test fixtures for
  `fir_filter`, `running_stats`, and `sliding_correlator` now pass
  `PYTHON=sys.executable` to `make`, ensuring CMake links the extension against
  the same Python SOABI that the test runner uses. Without this, MinGW's
  `sh.exe` could resolve `python3` to a different interpreter and produce a
  `.pyd` with a mismatched SOABI tag.
- **Windows — iqfile binary read corruption**: `07_demo.py` now opens the q15
  file with `O_BINARY` so the Windows UCRT does not perform CR/LF translation
  on binary sample data.
- **Uninitialized field-backed properties**: the generated `_core.c` now uses
  `calloc` instead of `malloc` in the `create()` function. Fields added after
  initial scaffolding via `jm property --field` are now guaranteed to be
  zero-initialised rather than containing heap garbage.

______________________________________________________________________

## \[0.10.4\] — 2026-05-12

### Added

- **PyMethodDef doctests**: all generated `ml_doc` strings now contain working
  Python doctests (exact scalar values for scalar returns, shape/dtype checks
  for array returns). Run them via `pytest --doctest-modules`.
- **Doxygen scaffolding**: `jm new` now writes a `Doxyfile` configured for the
  generated project so `doxygen` works out of the box.
- **`jm-run-tests` entry point**: `jm-run-tests` (bundled with the PyPI
  package) installs and runs the test suite via `uv run`, replacing per-CI
  ad-hoc install commands in all workflows.
- **`jm-install-deps` in all CI workflows**: `ci.yml` and `release.yml` now
  use the packaged `jm-install-deps` / `jm-run-tests` entry points instead of
  inline shell incantations.
- **Dynamic example discovery**: `_EXAMPLES` is now assembled at import time by
  walking `examples/` for subdirs that contain a `test.py`, so new examples are
  picked up without editing `_example.py`.

### Fixed

- **Windows — CMake Python3 detection**: the generated `Makefile` now derives
  `Python3_EXECUTABLE` via `sys.executable` (normalised to POSIX slashes with
  `pathlib`) instead of `which python3`. Git Bash's `which` returns a
  Unix-style path that Windows CMake 4.3 cannot execute.
- **Windows — example integration tests fully pass**: the generated `Makefile`
  now forces `-G "MinGW Makefiles"` when `OS=Windows_NT` so CMake picks `gcc`
  instead of MSVC (which rejects C99 `float complex`). Test fixtures no longer
  skip on Windows; `./demo` → `demo.exe`, DLL directory prepended to `PATH` at
  runtime, `-Wl,-rpath` omitted on Windows, and `pytest-benchmark` moved from
  required to optional deps in the generated `pyproject.toml` so `uv pip
  install -e .` no longer tries to overwrite the locked `pytest.exe` in the
  test runner venv.

______________________________________________________________________

## \[0.10.3\] — 2026-05-12

### Fixed

- **Gap #1 — `__init__.py` preservation**: `_regenerate_module` now merges new
  class/function exports into an existing `__init__.py` instead of
  overwriting it. User-written wrapper classes, docstrings, and other content
  below the re-export line are left untouched. Also handles the empty-import
  initial state (file created by `jm module` before any objects are added).
- **Gap #2 — `batch` flag persistence**: `batch = true` is now written to
  `just-makeit.toml` so regenerated methods use array-input wrappers
  (`METH_VARARGS`) rather than reverting to `METH_NOARGS`. The `--batch` flag
  is also wired through the CLI `method` command.
- **Gap #3 — C body preservation on regeneration**: `_regenerate_module` now
  extracts existing `static PyObject *` function bodies before overwriting
  `module_ext.c` and splices them back in afterwards. Brace-counting (not
  regex) handles parameters with nested parens such as `Py_UNUSED(ignored)`.
- **Gap #4 — `--no-step` in mixed modules**: objects scaffolded with
  `--no-step` no longer emit `step`/`steps` wrappers when co-resident in a
  module with objects that do have a step.
- **Gap #5 — phantom `module_core.h` include**: `module_ext.c` no longer
  unconditionally includes `<module>/<module>_core.h`. The include is emitted
  only when module-level functions are present (they are the only consumers
  of that header).
- **Gap #6 — CMakeLists external lib block propagation**: when a new object is
  added to a module, `if(VAR) target_link_libraries/target_include_directories …
  endif()` blocks found in sibling CMakeLists files are copied and adapted for
  the new component automatically.

### Added

- **+11 tests** (716 total): `tests/test_module_gaps.py` covering all six gaps.

______________________________________________________________________

## \[0.10.2\] — 2026-05-11

### Added

- **`--no-state` for `jm object`**: suppress the default auto-generated state
  variable, constructor arguments, and getter/setter scaffolding. Emits
  `<<IMPLEMENT>>` stubs in the C struct body and `create()`/`destroy()`/`reset()`
  so you fill in the real domain-specific constructor signature by hand.
  Mutually exclusive with `--state`. All downstream commands (`jm method`,
  `jm property`) detect the flag from TOML and regenerate correctly.
- **`--no-step` for `jm object`**: suppress `step()` and `steps()` from all C
  and Python output. Lifecycle scaffolding (`create`, `destroy`, `reset`) is
  still generated. The bench stub becomes a minimal printf with no volatile
  sink. Use for objects whose interface is entirely via named `jm method` calls
  (e.g. FIR filters, decimators, block processors).
- **`--out-type TYPE` and `--out-divisor N` for `jm method`**: allocate an
  output array per call and pass `*out` to the C stub automatically. The array
  length is `in_len / out_divisor`. Use `--out-divisor 2` for CI8/CI16/CI32
  inputs where two raw bytes form one complex output sample.
- **+21 tests** (705 total): `--arg-type T[]` standalone and in-module (21).

### Fixed

- **`--arg-type T[]` without `--return-type`** now correctly defaults to
  `void` for both standalone and in-module objects. Previously, omitting
  `--return-type` caused an internal error (the array element type was
  propagated as the return type, which is not a scalar and raised a
  `ValueError`/`KeyError` during template rendering). The fix affects four
  sites: `make_sample_ctx` (default logic), `_init.py` and `_object.py`
  (`make_step_ctx` call), and `_config.py` (TOML persistence — the bug also
  caused the wrong value to be written to `just-makeit.toml`, which broke
  module regeneration on reload).

______________________________________________________________________

## \[0.10.1\] — 2026-05-11

### Added

- **Type stubs** (`__init__.pyi`): every module subpackage now ships a
  generated `.pyi` alongside its `__init__.py`, kept in sync by every
  `object`, `method`, `property`, and `function` command. Standalone objects
  already had `.pyi` files; module objects now do too. Type maps: `float` /
  `double` → `float`, `*_Complex` → `complex`, arrays → `NDArray[np.dtype]`.
- **`--arg-type type[]` for objects**: objects whose primary operation
  processes a whole buffer in one call (decimators, packet framers, block
  codecs) can now declare their input as an array type. The C step receives
  `(const elem_t *x, size_t x_len)`; the Python wrapper uses
  `PyArray_FROM_OTF`; `steps()` is not generated (the primary op already takes
  a buffer). Supported element types: all scalar types accepted by `--arg-type`.
- **`install.sh` bootstrap**: `curl`-pipeable installer requiring no pre-existing
  tools (no uv, no pip). Detects Python ≥ 3.11, installs cmake + C compiler via
  the system package manager, creates a venv, and pip-installs just-makeit +
  numpy. Served from GitHub Pages for a short URL:
  `. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)`
  Sourcing via `. <(curl ...)` auto-activates the venv in the current shell.
  Supports `--check`, `--force`, and a custom venv path argument.
- **Example tests hardened**: all 8 bundled examples now assert type stubs are
  generated with correct signatures. `array_processing` adds pattern 5
  demonstrating `--arg-type type[]`. `test_readme_assembled.py` blocks stale
  example READMEs in CI.
- **Artifact CI tests all examples**: `artifact.yml` now runs
  `just-makeit example <name>` for every bundled example after PyPI install,
  testing the exact TL;DR shown in each README.
- **+8 tests** (668 total): README assembled checks (8).

______________________________________________________________________

## \[0.10.0\] — 2026-05-11

### Added

- **`--return-type void` for objects** (`jm new` / `jm object`): sink and
  side-effect objects are now fully supported. The generated bench compiles
  cleanly — no `volatile void` or `sizeof(void)`. `steps()` drops the output
  array parameter; Python `step()` / `steps()` return `None`.
- **Array parameters for methods and functions** (`--param name:type[]`):
  `jm method` and `jm function` now accept numpy array inputs. The C stub
  receives `(const elem_t *name, size_t name_len)`; the Python wrapper
  generates `PyArray_FROM_OTF` + `Py_DECREF` automatically. Supported element
  types: all fixed-width integer types, float, double, float _Complex,
  double _Complex. Mixed scalar + array params are supported in a single call.
- **CLI help overhauled**: `just-makeit help` now documents void return types,
  array `--param` syntax with elem-type list, and real-world examples
  (`execute_ctrl`, `apply_window`, sink/generator objects).
- **+41 tests** (612 total): void return (10), method array param (11),
  function array param (11), CLI void return (4), CLI array param (6),
  help content (9).

______________________________________________________________________

## \[0.9.13\] — 2026-05-11

### Added

- **`just-makeit example <name>`**: run any bundled example end-to-end in a
  temporary directory — no `git clone` required (`uvx just-makeit install-deps
  && just-makeit example fir_filter`). All 8 examples are now shipped inside
  the wheel under `just_makeit/examples/`.
- **Bench template DCE fix**: `(void)step(obj)` in warmup and step-timing loops
  is now `volatile <<return_ctype>> _sink = step(obj)` so the compiler cannot
  dead-code-eliminate the measured loop at any optimisation level. Applies to
  both the stateful-object bench and the pure-function bench.
- **Example TL;DR blocks updated**: all 8 example READMEs now show the
  `just-makeit example <name>` one-liner instead of the `git clone + python3
  test.py` form.

______________________________________________________________________

## \[0.9.12\] — 2026-05-11

### Fixed

- **Duplicate getter/setter declarations in `_core.h`**: when a `--state`
  variable also had a `jm property` added for it, both `make_state_ctx` and
  `make_properties_ctx` emitted declarations for the same C functions.
  `make_properties_ctx` now skips `property_decls` for any name already
  covered by `make_state_ctx`.

______________________________________________________________________

## \[0.9.11\] — 2026-05-11

### Fixed

- **Generated test scaffold**: `ALMOST_EQ_C(a, b, tol)` macro double-evaluated
  `a`, silently calling a stateful `step()` twice for complex-returning objects
  (LO, NCO, any `--return-type "float _Complex"`). Replaced with
  `_almost_eq_c` / `_almost_eq` static inline functions and thin macro wrappers
  so each argument is evaluated exactly once.
- **Release checklist**: GitHub Release step was missing; `release.yml` only
  publishes to PyPI and does not create GitHub Releases automatically.

______________________________________________________________________

## \[0.9.10\] — 2026-05-11

### Fixed

- **`property --field` on module objects** now updates `obj_core.h` in
  addition to regenerating `module_ext.c`; previously the struct field
  was silently omitted from the header.

______________________________________________________________________


## \[0.9.9\] — 2026-05-10

### Added

- **`jm-install-deps --check`** (`-Check` on Windows): reports what is already
  installed and what will be installed without making any changes; exits 1 if
  anything is missing, 0 if all present.
- **Prerequisites section** added to all seven example READMEs showing
  `jm-install-deps --check` / `jm-install-deps` / `source` workflow.

### Fixed

- **Windows: `uv tool install .` crash** — switched `just-makeit`'s own build
  backend from `just-buildit` to `hatchling`; `just-buildit` called
  `_python_link_flags()` unconditionally, which raises on Windows when the
  Python import library is absent (GitHub Actions hosted tool cache).
- **Windows: example build tests** — `test_examples.py` now skips on `win32`
  (MSVC rejects C99 `float complex`, same reason as `TestNewBuild`).
- **Windows: `UnicodeEncodeError` on cp1252 consoles** — replaced all Unicode
  arrows (`→`) with ASCII `->` in `_cli.py` (`_USAGE`), `_templates.py`
  (generated `README` and `*_core.h` lifecycle comment), and helper scripts.
- **`test_perf.py`**: bare `write_text()` replaced with `write_text(encoding="utf-8")`.

______________________________________________________________________


## \[0.9.7\] — 2026-05-10

### Added

- **`--arg-type void`** on `new --object` and `object` commands: generate a
  no-input (source/generator) object whose `step()` signature is
  `T comp_step(const comp_state_t *state)` with no input parameter, and whose
  `steps()` block processor is `void comp_steps(state, T *out, size_t n)`.
- **`method --multi-output T`** now wires secondary out-pointer parameters into
  the C stub declaration *and* the Python wrapper: stack-allocates each extra
  output, calls C with `&outN`, returns a `PyTuple_Pack` tuple.
- **`property --field`** on the `property` command: declares `T pname;` as a
  struct field in `comp_state_t` and auto-implements the getter as
  `return state->pname` and setter as `state->pname = v` — no manual
  `<<IMPLEMENT>>` stubs needed. Computed (non-`--field`) properties are
  unchanged.

### Fixed

- `clib_common.h` now installed to the include prefix alongside component
  headers; previously excluded by CMake install rules, causing `fatal error:
  'clib_common.h' file not found` when compiling external C consumers.
- `just-makeit add` now preserves field-backed property struct fields and
  method declarations when regenerating `_core.h` and `ext.c`.

______________________________________________________________________

## \[0.9.6\] — 2026-05-10

### Added

- `steps(x, out=buf)` zero-copy path: when an output buffer is passed to
  `steps()`, the C function writes directly into it and returns the same Python
  object (no allocation).  `out` is accepted by all stateful objects and
  pure-struct types.
- `array_processing` example: four array-processing patterns (auto-generated
  `steps()`, fixed-output method, variable-output method, multi-output method).

### Fixed

- `_perf.py`: `static inline → JM_FORCEINLINE JM_HOT` upgrade now patches
  `_core.h` (where the inline step lives).
- `_init.py`: `arg_type` / `return_type` now persisted to `just-makeit.toml`
  for standalone objects; previously the default `float _Complex` was reloaded
  when `just-makeit method` re-rendered `_core.h`.
- Step stub in `_core.h` restored to `const` state pointer (placeholder body
  does not mutate state), matching example patch-script expectations.
- `make_methods_ctx`: variable-output declarations now include the input
  parameter (`const arg_t *in, size_t n_in`) and multi-output extra params
  when `arg_type != void`.
- `fir_filter` and `sliding_correlator` example patch scripts: insertion-point
  regex updated to match the `steps_c_decl` docstring anchor; `steps()` body
  regex fixed (`void\s+` not `void\s*\n`).
- `test_steps_out_param` template: uses separate `obj1`/`obj2` instances so
  the stateful-filter comparison is valid across both calls.

______________________________________________________________________

## \[0.9.5\] — 2026-05-10

### Added

- **`just-makeit method <name>`** — add a named execute method to an object:
  scalar fixed-output (`return_type scalar_fn(state, arg x)`), variable-output
  (`size_t fn(state, [in, n_in,] ret *out)` with pre-allocated Python buffer),
  and multi-output (tuple of zero-copy NumPy views).
- **`just-makeit property <name>`** — add a named computed property backed by a
  C function; getter auto-registered in the Python type's `tp_getset`.
- **`--array-arg name:dtype`** on `object` / `new --object` — declares a
  constructor parameter that is a NumPy array (any NumPy dtype string accepted);
  C side receives a typed pointer, Python side passes an ndarray.
- **Function commands** — `just-makeit function` scaffolds a standalone
  pure-function extension (no state, no lifecycle) for simple numeric operations.

______________________________________________________________________

## \[0.9.4\] — 2026-05-10

### Fixed

- `artifact.yml` biquad spectral test: same `t/512` and `reset()` bugs fixed in v0.9.3 for the local test were also present in the CI smoke test.

______________________________________________________________________

## \[0.9.3\] — 2026-05-10

### Added

- `jm-install-deps` console script: detects OS, installs cmake + C compiler via system package manager, creates a venv at `/tmp/jm-venv` (configurable), installs numpy and just-makeit into it.
- `jm-docker-e2e` console script: runs the full artifact smoke test in a clean Docker container (mirrors `artifact.yml`).
- Both scripts are bundled in the wheel (`just_makeit/scripts/`) and exposed as proper `[project.scripts]` entry points.

### Fixed

- Generated `COMPONENT_TEST_C`: getter/setter checks now run before `step()` is called, so state-mutating `step()` implementations (e.g. running_stats incrementing `n`) no longer cause false failures.
- `filter_module` example smoke test: biquad spectral test used normalized time (`t/512`) instead of sample indices, placing both lo and hi signals well below the filter cutoff. Fixed to use sample indices so the stopband test is meaningful.
- `filter_module` example smoke test: `reset()` resets all state vars (including coefficients) to type defaults, not constructor values — fixed test to use a fresh `Biquad` instance for the stopband check instead of calling `reset()`.
- All example `test.py` files updated from `component=` to `object_name=` keyword arg.
- `pyproject.toml` pytest config: removed stale `--ignore` flags; all tests (including cmake-build example tests) now run in the default suite.
- `filter_module` README: added "What you'll need" prerequisites, `ctest` step in build instructions.

______________________________________________________________________

## \[0.9.2\] — 2026-05-10

### Fixed

- Generated `Makefile` now auto-installs `pytest` if not present (same pattern as numpy), so `make test` works in bare environments such as the post-release artifact smoke test.
- Removed the broken `unittest discover` fallback from both Makefile templates.  When pytest exits 5 (no tests collected — normal for the module workflow) `make test` now succeeds cleanly; any other non-zero exit propagates as a real failure.
- Removed `2>/dev/null` from the pytest invocation so failures are visible.

______________________________________________________________________

## \[0.9.1\] — 2026-05-10

### Fixed

- `artifact.yml` post-release smoke tests were failing due to stale `--component` / `just-makeit init` CLI references carried over from v0.8.x.  All references updated to `--object` / `just-makeit object` to match the v0.9.0 CLI.

______________________________________________________________________

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
