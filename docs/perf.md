# Performance annotations

Most projects don't need this page. The generated `step()` / `steps()` loop
is already cache-friendly and autovectorisable. Come here when you have a
tight inner loop that needs to go faster: you've profiled it, it's the
bottleneck, and the compiler's default output isn't good enough.

The performance layer is three tools:

1. **`jm_perf.h`** — function-level compiler hints (`JM_HOT`, `JM_FORCEINLINE`,
    `JM_RESTRICT`). Low-effort; minimal code change.
1. **`JM_DEFINE_STEPS`** — generates the outer dispatch loop so you write only
    `step()` and optionally a SIMD `step_batch()`. Medium effort; large payoff
    for algorithms with a fixed history depth.
1. **`jm_simd.h`** — width-portable SIMD operation macros that compile to
    AVX-512, AVX2, or scalar without `#ifdef` in your code. High effort; for
    when you need the last few percent.

All three are opt-in. A project with none of them still builds and runs.

______________________________________________________________________

## Enabling perf

**New project:**

```sh
just-makeit new my_filter --object filter --perf
```

**Existing project** (preserves all user code):

```sh
just-makeit perf
```

Once `perf = true` is recorded in `just-makeit.toml`, every subsequent
`object` and `add` inherits it automatically — including
`jm object --module M --perf`, which now writes `jm_perf.h` for module
objects too.

______________________________________________________________________

## `jm_perf.h` — compiler-hint macros

The cheapest performance win. Adding `JM_HOT` and `JM_FORCEINLINE` to
`step()` is a one-line change that signals hot-path intent to the compiler and
eliminates call overhead for the inner loop. On unknown compilers all macros
expand to safe no-ops.

| Macro            | Effect                                                      |
| ---------------- | ----------------------------------------------------------- |
| `JM_FORCEINLINE` | Forces inlining; eliminates call overhead on hot functions. |
| `JM_HOT`         | Marks a function as performance-critical.                   |
| `JM_LIKELY(x)`   | Hints that `x` is almost always true.                       |
| `JM_UNLIKELY(x)` | Hints that `x` is almost never true.                        |
| `JM_RESTRICT`    | Asserts no pointer aliasing; enables freer vectorisation.   |
| `JM_ALIGNED(n)`  | Aligns a variable or struct field to `n` bytes.             |

All macros expand to safe no-ops on unknown compilers. On x86, `jm_perf.h`
also includes `<immintrin.h>`; on aarch64 it includes `<arm_neon.h>` — so
SIMD intrinsics are available without an extra include either way.

______________________________________________________________________

## `JM_DEFINE_STEPS` — the dispatch macro

Reach for this when your algorithm has a fixed history depth (delay line,
coefficient buffer) and you want SIMD to kick in for block processing. The
macro owns the dispatch loop: scratch buffer management, SIMD stride, and
scalar tail. You write `step()` for correctness and optionally `step_batch()`
for throughput. The three tuning constants (`LENGTH`, `BATCH`, `CHUNK`) are
the only coupling between layers.

`JM_DEFINE_STEPS` stamps out `<fn>_steps()` — the outer dispatch loop —
so you never write it by hand.

### Generic form

```c
JM_DEFINE_STEPS(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)
```

| Parameter  | Concern     | Meaning                                                     |
| ---------- | ----------- | ----------------------------------------------------------- |
| `fn`       | —           | Name prefix; resolves `fn##_step()` and `fn##_step_batch()` |
| `state_t`  | —           | State struct type                                           |
| `sample_t` | —           | Per-sample type (e.g. `float complex`)                      |
| `LENGTH`   | algorithm   | History depth: samples held in `state->delay[]`             |
| `BATCH`    | parallelism | SIMD width in samples                                       |
| `CHUNK`    | tuning      | Samples per scratch-buffer fill                             |

`LENGTH`, `BATCH`, and `CHUNK` must be integer constant expressions (no VLA).

**What gets generated:**

- Whenever a SIMD tier is active (AVX-512, AVX2, or NEON on aarch64 — see
    the tier table below): fills a stack-resident scratch buffer
    (L1-resident, `LENGTH + CHUNK` samples), calls `fn##_step_batch()` in
    strides of `BATCH`, then falls through to the scalar tail via
    `fn##_step()`.
- On the scalar tier: loops directly over `fn##_step()`.

The three constants are the only coupling between layers. You write `step()`.
You optionally write `step_batch()` for SIMD. `JM_DEFINE_STEPS` owns the rest.

Convention: `state->delay[0..LENGTH-1]` is the delay line, `delay[0]` = newest.

!!! warning "`state->delay` must exist even for a stateless object (`LENGTH=0`)"

    Whenever a SIMD tier is active, the generated `steps()` references
    `state->delay[...]` in a loop that happens to run zero iterations when
    `LENGTH` is `0` — the member still has to exist for the translation unit
    to compile. A single-element placeholder field (e.g. `float delay[1]`)
    is enough. This is easy to miss on **aarch64**, where NEON is
    unconditionally active (unlike AVX2/AVX-512, which only turn on under
    explicit compiler flags) — a `LENGTH=0` object that always compiled
    fine on x86 can fail to build on aarch64 until you add the placeholder.

______________________________________________________________________

### FIR filter example

**In the component header** — define the constants and the SIMD kernel:

```c
#define FIR_TAPS   16              /* algorithm:   number of coefficients       */
#define FIR_LENGTH (FIR_TAPS - 1)  /* history:     samples held in delay[]      */
#define FIR_BATCH  8               /* parallelism: AVX-512 complex samples/call */

#ifdef __AVX512F__
JM_FORCEINLINE JM_HOT void
fir_filter_step_batch(
    fir_filter_state_t     *state,
    const float complex    *window,
    float complex          *out)
{
    __m512 vg  = _mm512_set1_ps(state->gain);
    __m512 acc = _mm512_setzero_ps();
    for (int k = 0; k < FIR_TAPS; k++) {
        __m512 vx = _mm512_loadu_ps((const float *)(window + FIR_LENGTH - k));
        acc = _mm512_fmadd_ps(_mm512_set1_ps(state->coeffs[k]), vx, acc);
    }
    _mm512_storeu_ps((float *)out, _mm512_mul_ps(acc, vg));
}
#endif
```

`FIR_LENGTH` is what the macro sees — the history depth. `FIR_TAPS` is the
FIR-specific concept; `step_batch()` loops over it, and the window index
`FIR_LENGTH - k` reaches back exactly `TAPS` samples (index 0 = oldest
history, index `FIR_LENGTH` = current sample).

**In the component source** — tune the chunk size and generate `steps()`:

```c
#define FIR_CHUNK 256  /* tuning: samples per scratch-buffer fill */

JM_DEFINE_STEPS(fir_filter, fir_filter_state_t, float complex,
                FIR_LENGTH, FIR_BATCH, FIR_CHUNK)
```

See the [FIR filter example](examples/fir_filter.md) for a complete
walkthrough including benchmarks.

______________________________________________________________________

## `jm_simd.h` — width-portable operation macros

The highest-effort option — worth it when the inner loop matters more than the
dispatch. Raw AVX/NEON intrinsics lock `step_batch()` to one ISA and require
`#ifdef` guards for every other target. `jm_simd.h` provides macros that
select the widest available instruction set at compile time — AVX-512,
AVX2+FMA, NEON (aarch64), or scalar — so the same source compiles everywhere.
The tier is chosen once at the top of `jm_simd.h`; user code sees no `#ifdef`.

Included automatically by `jm_perf.h`; can also be included standalone.

### SIMD tier selection

| Tier       | `JM_SIMD_WIDTH_F32` | `JM_VEC_F32`  | `JM_VEC_F64`  |
| ---------- | ------------------- | ------------- | ------------- |
| AVX-512F   | 16                  | `__m512`      | `__m512d`     |
| AVX2 + FMA | 8                   | `__m256`      | `__m256d`     |
| NEON       | 4                   | `float32x4_t` | `float64x2_t` |
| Scalar     | 1                   | `float`       | `double`      |

`JM_SIMD_WIDTH_F64` is always half of `JM_SIMD_WIDTH_F32`.

NEON is selected on any `__aarch64__` build — unlike AVX2/AVX-512, which
require explicit compiler flags (`-march=native` or similar) to turn on,
NEON is part of the mandatory ARMv8-A baseline, so this tier is **always**
active on aarch64 Linux/macOS, opt-in or not. 32-bit ARM is out of scope
(no double-precision NEON there).

### Operation macros

| Macro                                           | Operation                                                                      |
| ----------------------------------------------- | ------------------------------------------------------------------------------ |
| `JM_ZERO_F32()` / `JM_ZERO_F64()`               | Zero accumulator                                                               |
| `JM_SPLAT_F32(x)` / `JM_SPLAT_F64(x)`           | Broadcast scalar to all lanes                                                  |
| `JM_LOAD_F32(ptr)` / `JM_LOAD_F64(ptr)`         | Unaligned load                                                                 |
| `JM_STORE_F32(ptr, v)` / `JM_STORE_F64(ptr, v)` | Store                                                                          |
| `JM_ADD_F32(a, b)` / `JM_ADD_F64(a, b)`         | Element-wise add                                                               |
| `JM_MUL_F32(a, b)` / `JM_MUL_F64(a, b)`         | Element-wise multiply                                                          |
| `JM_FMA_F32(acc, a, b)` / `JM_FMA_F64(...)`     | `acc += a * b`                                                                 |
| `JM_MAC_F32(acc, ptr, s)` / `JM_MAC_F64(...)`   | Load `JM_SIMD_WIDTH_F32` floats from `ptr`, multiply by scalar `s`, accumulate |
| `JM_HSUM_F32(v)` / `JM_HSUM_F64(v)`             | Horizontal reduce all lanes to one scalar                                      |

### Dot product helper

```c
float  jm_dot_f32(const float  *a, const float  *b, int n);
double jm_dot_f64(const double *a, const double *b, int n);
```

Handles the SIMD loop and scalar tail automatically.

### FIR inner loop example

```c
/* step_batch: compute one output sample from JM_SIMD_WIDTH_F32 inputs */
JM_FORCEINLINE JM_HOT void
fir_step_batch(fir_state_t *state, const float *window, float *out)
{
    JM_VEC_F32 acc = JM_ZERO_F32();
    for (int k = 0; k < N_TAPS; k++)
        JM_MAC_F32(acc, window + k, state->coeffs[k]);
    *out = JM_HSUM_F32(acc) * state->gain;
}
```

On AVX-512 this expands to `_mm512_fmadd_ps` + `_mm512_reduce_add_ps`. On
aarch64 it expands to `vfmaq_f32` + `vaddvq_f32`. On scalar it compiles to a
plain loop the compiler can auto-vectorise. No `#ifdef` in user code; the
tier is chosen once at the top of `jm_simd.h`.

### Additional hint macros (in `jm_perf.h`)

| Macro                       | Effect                                                                     |
| --------------------------- | -------------------------------------------------------------------------- |
| `JM_UNROLL(n)`              | `#pragma GCC unroll n` — ask compiler to unroll loop `n` times             |
| `JM_ASSUME_ALIGNED(ptr, n)` | `__builtin_assume_aligned` — enables SIMD loads without alignment penalty  |
| `JM_PREFETCH(ptr, rw, loc)` | `__builtin_prefetch` — software prefetch; `rw`=0 read / 1 write, `loc`=0–3 |

______________________________________________________________________

## SIMD build flag

SIMD intrinsics require `-march=native -ffast-math`. Pass `-DENABLE_SIMD=ON`
to CMake:

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON
cmake --build build --parallel
```
