# Roadmap

## Vision

just-makeit is the fastest path from algorithm idea to production Python C extension.

Zero boilerplate. Full test coverage from day one. Just works.

The goal is simple: you should be able to think of an algorithm, run one command,
and have a complete, tested, packagable C extension project — with a clean C library
that can also be linked from Rust, C++, or anything else. The scaffolding should
disappear. Your algorithm should be all that remains.

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

## v0.3 — Pure / stateless mode ✓ shipped

Not every algorithm needs state. Some are better expressed as a pure function
with no lifecycle overhead.

**Planned and delivered:**

- `--pure` flag on `new` and `init` — auto-detects style from declared params:
  - **Scalar-only params** → scalar style: params passed per call as function
    arguments; Python exports module-level `comp(x, **params)` and
    `comp.steps(arr, **params)` functions.
  - **Any array param** → struct style: caller-managed `comp_params_t` with
    `_params_create()` (calloc), `_params_free()`, `_params_init()` (for
    stack/pool/`aligned_alloc`/`mmap` allocation patterns); Python exposes a
    callable class (`obj(x)` via `tp_call`, context-manager support).
- `--param` flag — idiomatic synonym for `--state` when used with `--pure`;
  both are accepted everywhere with identical semantics.
- `pure` field in `just-makeit.toml` — persisted so `just-makeit add --param`
  regenerates the correct template set; auto-promotes scalar→struct if an
  array param is added.
- `docs/pure.md` — full cost/benefit analysis, allocation pattern guide with
  concrete C examples (heap, stack, SIMD-aligned, N-channel array, arena,
  mmap), and a Mermaid decision flowchart.

**Delivered beyond plan:**

- C and Python benchmarks generated with every component (`make bench`,
  `make bench-save`, `make bench-compare`; pytest-benchmark + doppler-style
  C binary).  Bench files are in `_STATE_TEMPLATES` so they regenerate on
  `just-makeit add`.
- `examples/` end-to-end test runner — `tests/test_examples.py`
  auto-discovers `examples/*/test.py`; `test_all_examples_have_test_py`
  enforces that every example directory ships a test driver.
- `examples/README.md` — contributor guide explaining the `.steps/` naming
  convention, `assemble.py` weaving, and the `test.py` contract.
- `examples/fir_filter` step 8 — pure FIR variant demonstrating struct-style
  caller-managed params and multi-channel usage.
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

| Tier | `JM_SIMD_WIDTH_F32` | `JM_VEC_F32` |
|---|---|---|
| AVX-512F | 16 | `__m512` |
| AVX2 + FMA | 8 | `__m256` |
| Scalar | 1 | `float` |

**Full macro set:**

| Macro | Operation |
|---|---|
| `JM_VEC_F32` / `JM_VEC_F64` | Width-aware vector type |
| `JM_ZERO_F32()` | Zero accumulator |
| `JM_SPLAT_F32(x)` | Broadcast scalar to all lanes |
| `JM_LOAD_F32(ptr)` | Unaligned load |
| `JM_STORE_F32(ptr, v)` | Store |
| `JM_ADD_F32(a, b)` | Element-wise add |
| `JM_MUL_F32(a, b)` | Element-wise multiply |
| `JM_FMA_F32(acc, a, b)` | `acc += a * b` |
| `JM_MAC_F32(acc, ptr, s)` | Load + FMA in one call |
| `JM_HSUM_F32(v)` | Horizontal reduce to scalar |
| `jm_dot_f32(a, b, n)` | Full dot product loop |

`_F64` variants exist for all macros. `jm_perf.h` includes `jm_simd.h`
automatically; it can also be included standalone.

**Also added to `jm_perf.h`:**

| Macro | Effect |
|---|---|
| `JM_UNROLL(n)` | Loop unroll hint (`#pragma GCC unroll n`) |
| `JM_ASSUME_ALIGNED(ptr, n)` | Pointer alignment assertion for auto-vectorisation |
| `JM_PREFETCH(ptr, rw, loc)` | Software prefetch (`__builtin_prefetch`) |

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

## Ideas under consideration

These are not yet scheduled but are worth tracking:

- **NumPy ufunc registration** — `--ufunc` flag wraps `comp_fn` as a proper
  NumPy generalized ufunc, enabling broadcasting and `out=` support
- **Windows / MSVC CI template** — `just-makeit new` optionally generates a
  GitHub Actions workflow with a Windows runner
- **Interactive wizard** — `just-makeit new` without arguments drops into a
  short prompt-driven setup for users who prefer guided over CLI flags
