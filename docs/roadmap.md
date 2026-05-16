# Roadmap

## Vision

You have an algorithm. You want it in Python, fast, with tests and a clean C API
that C++, Rust, or any other language can also link against.

That path usually involves a week of CMake wrangling, Python C API spelunking,
and test infrastructure from scratch. just-makeit compresses it into one command.
The scaffolding disappears. Your algorithm is all that remains.

______________________________________________________________________

## v0.2 — Performance scaffold ✓ shipped

Real algorithms are hot. The generated code should be ready for it.

**Planned and delivered:**

- `--perf` flag on `new` and `init` — generates `jm_perf.h` with
  `JM_FORCEINLINE`, `JM_HOT`, `JM_LIKELY`, `JM_UNLIKELY`, `JM_RESTRICT`,
  `JM_ALIGNED`.  All macros are C99-compatible with safe no-op fallbacks.
- `ENABLE_SIMD` CMake option — enables `-march=native -ffast-math` on
  GCC/Clang.  Off by default; opt in per build.
- `/* #pragma omp simd */` annotation on the generated `steps()` loop.

**Delivered beyond plan:**

- `just-makeit perf` command — upgrades an existing project in-place without
  touching any user-written code.  The `--perf` flag is for new scaffolds;
  `just-makeit perf` is for projects already in progress.
- `JM_DEFINE_STEPS(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)` macro —
  stamps out the outer dispatch loop from three separated concerns: algorithm
  history depth, SIMD batch width, and scratch-buffer tuning.  The user writes
  `step()`.  The macro generates everything else.
- `sliding_correlator` example — proves `JM_DEFINE_STEPS` is
  algorithm-agnostic: complex cross-correlation with a different state layout
  and complex multiply uses the exact same macro invocation as the FIR filter.
- `docs/perf.md` — full reference for the macro set and `JM_DEFINE_STEPS`.

______________________________________________________________________

## v0.3 — Benchmarks and example test runner ✓ shipped

**Delivered:**

- C and Python benchmarks generated with every component (`make bench`,
  `make bench-save`, `make bench-compare`; pytest-benchmark + doppler-style
  C binary).  Bench files are in `_STATE_TEMPLATES` so they regenerate on
  `just-makeit add`.
- `examples/` end-to-end test runner — `tests/test_examples.py`
  auto-discovers `examples/*/test.py`; `test_all_examples_have_test_py`
  enforces that every example directory ships a test driver.
- `examples/README.md` — contributor guide explaining the `.steps/` naming
  convention, `assemble.py` weaving, and the `test.py` contract.
- `docs/examples/` retired — stale duplicate of `examples/*/README.md`.

______________________________________________________________________

## v0.4 — C library distribution ✓ shipped

Today just-makeit targets Python consumers. v0.4 makes the generated project
a first-class C library too — distributable to C, C++, and Rust via the
standard mechanisms, with no changes to user-written algorithm code.

**Core idea:** each component's `_core.c` compiles once (as a CMake OBJECT
library) and links into *both* the Python DSO and a combined `libmy_dsp.so`.
No duplicated object files. No diverging codebases.

```mermaid
flowchart TD
    SRC["**your C source**\ngain_core.c · bpf_core.c · …"]

    SRC --> CLIB["**libmy_dsp.so**\ncombined shared library"]
    SRC --> PY["**Python package**\ngain.cpython-*.so\nbpf.cpython-*.so"]

    CLIB --> C["**C / C++ / Rust / …**\npkg-config · find_package"]
    PY   --> PYUSER["**Python**\npip install .\nfrom my_dsp import Gain"]
```

**Delivered:**

- Each component's `CMakeLists.txt` gains an OBJECT library target
  (`gain_core` OBJECT); the Python DSO and bench binary link against
  `$<TARGET_OBJECTS:gain_core>` instead of a static archive.
- Top-level `CMakeLists.txt` accumulates all OBJECT targets into
  `libmy_dsp.so` and adds `install()` rules for the library, headers,
  pkg-config file, and CMake config package.
- `just-makeit init` patches `target_sources(${PROJECT_NAME}_lib …)` in the
  top-level alongside the existing `add_subdirectory` patch.
- `cmake/<project>.pc.in` — pkg-config template.
- `cmake/<project>-config.cmake.in` — CMake `find_package` template.
- `native/inc/<project>.h` — umbrella header.

**Install story:**

```sh
cmake --install build --prefix /usr/local
gcc $(pkg-config --cflags --libs my-dsp) main.c -o main
```

```cmake
find_package(my-dsp REQUIRED)
target_link_libraries(my_app PRIVATE my_dsp::my_dsp)
```

______________________________________________________________________

## v0.5 — SIMD operation macros (`jm_simd.h`) ✓ shipped

`--perf` already gives you `JM_DEFINE_STEPS` for the outer dispatch loop.
v0.5 fills in the inner loop: a new `jm_simd.h` header provides
width-portable operation macros so `step_batch()` can be written once and
compile to AVX-512, AVX2, or scalar without any `#ifdef` in user code.

**What you get:**

```c
/* Before v0.5 — raw intrinsics, architecture-locked */
__m512 acc = _mm512_setzero_ps();
for (int k = 0; k < N_TAPS; k++)
    acc = _mm512_fmadd_ps(_mm512_set1_ps(state->coeffs[k]),
                          _mm512_loadu_ps(window + k), acc);
*out = _mm512_reduce_add_ps(acc);

/* After v0.5 — portable, compiles to best available ISA */
JM_VEC_F32 acc = JM_ZERO_F32();
for (int k = 0; k < N_TAPS; k++)
    JM_MAC_F32(acc, window + k, state->coeffs[k]);
*out = JM_HSUM_F32(acc);
```

**SIMD tiers selected at compile time:**

| Tier       | `JM_SIMD_WIDTH_F32` | `JM_VEC_F32` |
| ---------- | ------------------- | ------------ |
| AVX-512F   | 16                  | `__m512`     |
| AVX2 + FMA | 8                   | `__m256`     |
| Scalar     | 1                   | `float`      |

**Full macro set:**

| Macro                       | Operation                     |
| --------------------------- | ----------------------------- |
| `JM_VEC_F32` / `JM_VEC_F64` | Width-aware vector type       |
| `JM_ZERO_F32()`             | Zero accumulator              |
| `JM_SPLAT_F32(x)`           | Broadcast scalar to all lanes |
| `JM_LOAD_F32(ptr)`          | Unaligned load                |
| `JM_STORE_F32(ptr, v)`      | Store                         |
| `JM_ADD_F32(a, b)`          | Element-wise add              |
| `JM_MUL_F32(a, b)`          | Element-wise multiply         |
| `JM_FMA_F32(acc, a, b)`     | `acc += a * b`                |
| `JM_MAC_F32(acc, ptr, s)`   | Load + FMA in one call        |
| `JM_HSUM_F32(v)`            | Horizontal reduce to scalar   |
| `jm_dot_f32(a, b, n)`       | Full dot product loop         |

`_F64` variants exist for all macros. `jm_perf.h` includes `jm_simd.h`
automatically; it can also be included standalone.

**Also added to `jm_perf.h`:**

| Macro                       | Effect                                             |
| --------------------------- | -------------------------------------------------- |
| `JM_UNROLL(n)`              | Loop unroll hint (`#pragma GCC unroll n`)          |
| `JM_ASSUME_ALIGNED(ptr, n)` | Pointer alignment assertion for auto-vectorisation |
| `JM_PREFETCH(ptr, rw, loc)` | Software prefetch (`__builtin_prefetch`)           |

**Note on zero-dependency wheels:** the v0.4 OBJECT library design already
solves this — Python DSOs embed the C code directly and have no runtime
dependency on `libmy_dsp.so`. Pre-compiled wheel distribution is handled by
`cibuildwheel`, which the end project configures for its own platform targets.

______________________________________________________________________

## v0.6 — Type-parameterised I/O ✓ shipped

Generated `step()` signatures were hardcoded to `float complex`.  Real
algorithms use whatever type fits: `float`, `double`, `float _Complex`,
`double _Complex`.  v0.6 makes the I/O types explicit flags.

**Delivered:**

- `--arg-type TYPE` and `--return-type TYPE` on `just-makeit new` and
  `just-makeit init` — supported types: `float`, `double`, `float _Complex`,
  `double _Complex`.  Both default to `float _Complex` for backward
  compatibility.
- All generated artifacts (C header, Python binding, `.pyi` stub, C test,
  benchmarks, NumPy `steps()` loop) derive types from the declared flags.
  No manual patching needed after scaffolding.
- `arg_type` / `return_type` persisted in `just-makeit.toml` and read back by
  `just-makeit add` so regenerated files stay consistent.
- `examples/sliding_power` — demonstrates `--return-type float`: the estimator
  receives `float complex` samples and returns real-valued signal power.

______________________________________________________________________

## v0.6.1 — Multi-component quality-of-life ✓ shipped

**Fixed:**

- `just-makeit init` now automatically splices the new component's
  `from .comp import Comp` import and `__all__` entry into the existing
  `src/<pkg>/__init__.py`.  Handles absent `__all__`, multi-line `__all__`,
  and user additions; idempotent.
- Generated `pyproject.toml` lists `pytest-benchmark` as a `dependencies`
  entry so `pip install .` provides everything needed to run `make bench`.
- `JM_UNROLL` comment corrected: directive (unconditionally obeyed), not an
  advisory hint like `JM_HOT`.

**Added:**

- `examples/dsp_toolkit` — two-component library (Gain + EMA) that exercises
  the full `new` → `init` → build → test → use workflow in CI.
- `docs/workflow.md` rewritten around two end-to-end scenarios.

______________________________________________________________________

## v0.6.2 — CI split ✓ shipped

**Changed:**

- Post-publish smoke tests extracted into a dedicated `artifact.yml` workflow,
  triggered via `workflow_run` on Release success.  `release.yml` now handles
  `test → build → publish` only; artifact tests run after PyPI propagation.

______________________________________________________________________

## v0.6.3 — Realistic artifact CI ✓ shipped

**Changed:**

- `artifact.yml` rewritten around the `fir_filter` example — a real algorithm
  with array state (`coeffs`, `delay`) and a scalar param (`gain`).  The full
  workflow runs in CI: scaffold → implement `fir_filter_step` → `just-makeit perf`
  → `make && make test` → `just-makeit init gain` with `__init__.py` splice check
  → `cmake --install` → pkg-config consumer → CMake `find_package` consumer.
  The C consumers assert a correct impulse response `[0.25, 0.50, 0.25, 0.0, …]`.

______________________________________________________________________

## v0.7 — C library distribution ✓ shipped

Every just-makeit project is also a distributable C library — consumable from
Rust, C++, or pure C with no Python dependency.  The same C code drives both
the Python extension and the combined shared library: each component compiles
once as a CMake OBJECT library and links into both.

**Delivered:**

- `cmake/<project>.pc.in` and `cmake/<project>-config.cmake.in` — pkg-config
  and CMake `find_package` templates generated for every new project.
- `native/inc/<project>.h` — umbrella header for C consumers.
- `cmake --install` wires headers, library, and config files into the prefix.
  End users find the library with `pkg-config --cflags --libs my-project` or
  `find_package(my_project REQUIRED)`.
- `docs/c-library.md` — dedicated guide for C library consumers.

______________________________________________________________________

## v0.8 — Module subpackages ✓ shipped

A common DSP pattern is a collection of related types — `Fir`, `Biquad`,
`Equalizer` — that belong together in one import path but are independent at
runtime.  Before v0.8, each type required its own `.so`.  v0.8 introduces
extension modules: multiple types in one `.so`, one clean subpackage import.

**Delivered:**

- **`just-makeit module <name>`** — scaffold a named extension module
  (subpackage `.so`) that groups multiple types.
- **`just-makeit object <name> --module <name>`** — add a type to an existing
  module.  Each type has its own C core, test, and bench; the module's
  `_ext.c` is fully regenerated from the complete type list every time, so
  adding a third type never disturbs existing ones.
- Types within a module may have different `--arg-type`/`--return-type` values.
- Generated C tests now use a `CHECK()` macro counter instead of `assert()`:
  failures print `FAIL file:line expr` and exit nonzero — no silent pass under
  `-DNDEBUG`.

______________________________________________________________________

## v0.9 — CLI unification and `example` command ✓ shipped

**Breaking changes:**

- `just-makeit init` removed — replaced by `just-makeit object <name>`.
- `--component` renamed to `--object` throughout.

The generated C code is unchanged.  `object` is now the single verb for adding
any Python type, whether standalone or grouped in a module.

**Delivered:**

- **`just-makeit example <name>`** — run any bundled example end-to-end in a
  temporary directory, with no `git clone` required.  All bundled examples are
  shipped inside the wheel under `just_makeit/examples/`.
- **`--arg-type void`** on `object` — generate a source/generator object whose
  `step()` takes no input and produces a sample (NCO, noise source, etc.).
- **`property --field`** — declares a struct field and auto-implements the
  getter/setter; no manual stub needed.
- **`method --multi-output T`** — secondary out-pointer parameters wired into
  C stub and Python wrapper automatically, returned as a tuple.
- `just-makeit new --module <name>` — scaffold one or more empty modules in the
  same step as the project.

______________________________________________________________________

## v0.10 — Array I/O types and bootstrap installer ✓ shipped

**Delivered:**

- **`--arg-type type[]` for objects** — objects whose primary operation
  processes a whole buffer (decimators, packet framers) declare array input
  directly.  The C step receives `(const elem_t *x, size_t x_len)`;
  `steps()` is not generated (the primary op already takes a buffer).
- **`--return-type void` for objects** — sink objects that consume input but
  produce no output.
- **`--arg-type type[]` for methods and functions** — `jm method` and
  `jm function` now accept numpy array inputs.
- **Module type stubs** — every module subpackage now ships a generated `.pyi`
  alongside its `__init__.py`, kept in sync by every `object`, `method`,
  `property`, and `function` command.
- **`install.sh` bootstrap** — `curl`-pipeable installer that requires no
  pre-existing tools.  Detects Python ≥ 3.11, installs CMake and a C compiler
  via the system package manager, creates a venv, and installs just-makeit.
  Sourcing via `. <(curl ...)` auto-activates the venv in the current shell.

______________________________________________________________________

## v0.11 — Full I/O flexibility ✓ shipped

Every combination of scalar, array, and void inputs and outputs is now
supported.  The boundary between "what kind of object can I build?" and "what
do I actually need?" is effectively gone.

**Delivered:**

- **`--return-type void` on methods and functions** — cleanly generated; no
  `volatile void` or `sizeof(void)` in bench code.
- **Array parameters on methods and functions** — mixed scalar + array params
  in a single call.  The C stub receives `(const elem_t *name, size_t
  name_len)` for each array param; the Python wrapper handles
  `PyArray_FROM_OTF` automatically.
- **CLI help overhauled** — `just-makeit help` documents all I/O shapes with
  real-world examples.
- **`make test` now uses `unittest` by default** — projects scaffolded without
  `--pytest` use `python -m unittest discover`.  pytest is only invoked when
  `--pytest` was passed.

______________________________________________________________________

## What we're thinking about next

These are problems, not promises.  If any of them sounds like something you
run into, we'd like to hear about it.

**CI setup parity with scaffolding.** Running `just-makeit new` gives you a
fully tested project in under a minute.  Getting CI running for it still
requires hand-writing a GitHub Actions or Woodpecker workflow.  A
`just-makeit ci --provider github|woodpecker` command could close that gap.

**Broadcasting without boilerplate.** The generated `steps()` loop is fast and
correct, but it doesn't plug into NumPy's generalized ufunc machinery.  A
`--ufunc` flag could expose `comp_fn` as a proper ufunc, enabling `out=`
support and broadcasting — useful for filter banks and vectorized pipelines.

**Windows developer friction.**  The Windows build path works (MinGW, MSYS2,
Docker images), but it has more rough edges than the Linux/macOS path — symbol
visibility, DLL loading, and path handling all need extra attention.  Getting
to parity means documentation, CI coverage, and probably a few generated-file
fixes.
