# Roadmap

## Vision

just-makeit is the fastest path from algorithm idea to production Python C extension.

Zero boilerplate. Full test coverage from day one. Just works.

The goal is simple: you should be able to think of an algorithm, run one command,
and have a complete, tested, packagable C extension project — with a clean C library
that can also be linked from Rust, C++, or anything else. The scaffolding should
disappear. Your algorithm should be all that remains.

______________________________________________________________________

## v0.2 — Performance scaffold

Real algorithms are hot. The generated code should be ready for it.

**`--perf` flag** on `new` and `init` generates a `jm_perf.h` header alongside
the common headers. All macros are C99-compatible: compiler-extension paths are
gated behind `#if defined(...)` guards, with safe no-op fallbacks for unknown
compilers.

```c
/* GCC / Clang */
#if defined(__GNUC__) || defined(__clang__)
#  define JM_LIKELY(x)     __builtin_expect(!!(x), 1)
#  define JM_UNLIKELY(x)   __builtin_expect(!!(x), 0)
#  define JM_RESTRICT      restrict            /* C99 keyword */
#  define JM_FORCEINLINE   __attribute__((always_inline)) inline
#  define JM_ALIGNED(n)    __attribute__((aligned(n)))
#  define JM_HOT           __attribute__((hot))

/* MSVC */
#elif defined(_MSC_VER)
#  define JM_LIKELY(x)     (x)
#  define JM_UNLIKELY(x)   (x)
#  define JM_RESTRICT      __restrict
#  define JM_FORCEINLINE   __forceinline
#  define JM_ALIGNED(n)    __declspec(align(n))
#  define JM_HOT

/* Unknown / strict C99 — safe no-ops */
#else
#  define JM_LIKELY(x)     (x)
#  define JM_UNLIKELY(x)   (x)
#  define JM_RESTRICT      restrict
#  define JM_FORCEINLINE   inline
#  define JM_ALIGNED(n)
#  define JM_HOT
#endif
```

**SIMD cmake option** — `cmake -DENABLE_SIMD=ON` enables `-march=native -ffast-math`
(GCC/Clang) or `/arch:AVX2 /fp:fast` (MSVC). Off by default; opt in per build.

**`steps()` loop annotation** — the generated block-processing loop gets a
`/* #pragma omp simd */` comment so enabling it is a one-line opt-in.

Zero runtime dependencies — purely compile-time hints that fall back gracefully
on compilers that don't support them.

______________________________________________________________________

## v0.3 — Pure / stateless mode

Not every algorithm needs state. Some — especially those destined for cloud
functions, k8s sidecars, or NumPy ufuncs — are better expressed as a pure
function with no lifecycle overhead.

**`--pure` flag** on `new` and `init`:

```sh
just-makeit new my_ops --component normalize --pure
```

Generates a stateless component — no state struct, no create/destroy, no
reset. Just a function:

```c
/* normalize_core.h */
float complex normalize_fn(float complex x, double scale);
```

```python
# Python binding — module-level function, not a class
from my_ops import normalize
y = normalize(x, scale=1.0)         # single sample
y = normalize.steps(arr, scale=1.0) # block, returns ndarray
```

Natural fit for:

- k8s sidecar microservices with a minimal Python surface
- Cloud functions where object lifecycle is noise
- NumPy ufunc registration (v0.3.x follow-on)
- Functional pipelines where immutability matters

______________________________________________________________________

## v0.4 — C library distribution

Today just-makeit targets Python consumers. v0.4 makes the generated project
a first-class C library too — distributable to C, C++, and Rust via the
standard mechanisms.

**New generated artifacts:**

```
my_dsp/
├── cmake/
│   ├── my-dsp.pc.in              # pkg-config template
│   └── my-dsp-config.cmake.in   # CMake find_package template
├── native/
│   ├── inc/
│   │   ├── my_dsp.h              # umbrella — includes all component headers
│   │   └── …
```

**Combined shared library** — all components compile as OBJECT libraries and
link into a single `libmy_dsp.so` alongside the per-component Python DSOs.

**Install story:**

```sh
cmake --install build --prefix /usr/local
gcc $(pkg-config --cflags --libs my-dsp) main.c -o main
```

```cmake
find_package(my-dsp REQUIRED)
target_link_libraries(my_app PRIVATE my_dsp::my_dsp)
```

C/C++/Rust consumers get a proper library. Python consumers get the same `.so`
they always had. One codebase, two distribution paths.

______________________________________________________________________

## v0.5 — Zero-dependency wheels

Today `pip install my_dsp` requires a C compiler and CMake on the target
machine. v0.5 eliminates that.

**Static wheel mode** — just-buildit gains a `static = true` build option that
statically links the C core into each Python DSO. The resulting wheel contains
only `.cpython-*.so` files and no external `.so` dependencies.

```sh
just-makeit build --static   # → dist/my_dsp-0.1.0-cp311-cp311-linux_x86_64.whl
pip install dist/*.whl        # works anywhere — no compiler, no CMake
```

Pre-compiled wheel distribution on PyPI becomes straightforward: build once
in CI, ship everywhere.

______________________________________________________________________

## Ideas under consideration

These are not yet scheduled but are worth tracking:

- **NumPy ufunc registration** — `--ufunc` flag wraps `comp_fn` as a proper
  NumPy generalized ufunc, enabling broadcasting and `out=` support
- **Type specialisation** — generate separate `float` and `double` variants
  of a component from a single template
- **Windows / MSVC CI template** — `just-makeit new` optionally generates a
  GitHub Actions workflow with a Windows runner
- **Interactive wizard** — `just-makeit new` without arguments drops into a
  short prompt-driven setup for users who prefer guided over CLI flags
