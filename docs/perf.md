# Performance annotations

`just-makeit` ships a set of compiler-hint macros and a dispatch pattern that
can significantly accelerate hot DSP loops.  Performance is opt-in: plain
projects build and run correctly without any of it.

## Enabling perf

**New project:**

```sh
just-makeit new my_filter --component filter --perf
```

**Existing project** (preserves all user code):

```sh
just-makeit perf
```

Once `perf = true` is recorded in `just-makeit.toml`, every subsequent
`init` and `add` inherits it automatically.

---

## `jm_perf.h` — compiler-hint macros

| Macro            | Effect                                                      |
| ---------------- | ----------------------------------------------------------- |
| `JM_FORCEINLINE` | Forces inlining; eliminates call overhead on hot functions. |
| `JM_HOT`         | Marks a function as performance-critical.                   |
| `JM_LIKELY(x)`   | Hints that `x` is almost always true.                       |
| `JM_UNLIKELY(x)` | Hints that `x` is almost never true.                        |
| `JM_RESTRICT`    | Asserts no pointer aliasing; enables freer vectorisation.   |
| `JM_ALIGNED(n)`  | Aligns a variable or struct field to `n` bytes.             |

All macros expand to safe no-ops on unknown compilers.  On x86, `jm_perf.h`
also includes `<immintrin.h>` so SIMD intrinsics are available without an
extra include.

---

## `JM_DEFINE_STEPS` — the dispatch macro

`JM_DEFINE_STEPS` stamps out `<fn>_steps()` — the outer dispatch loop —
so you never write it by hand.

### Generic form

```c
JM_DEFINE_STEPS(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)
```

| Parameter  | Concern      | Meaning                                      |
| ---------- | ------------ | -------------------------------------------- |
| `fn`       | —            | Name prefix; resolves `fn##_step()` and `fn##_step_batch()` |
| `state_t`  | —            | State struct type                            |
| `sample_t` | —            | Per-sample type (e.g. `float complex`)       |
| `LENGTH`   | algorithm    | History depth: samples held in `state->delay[]` |
| `BATCH`    | parallelism  | SIMD width in samples                        |
| `CHUNK`    | tuning       | Samples per scratch-buffer fill              |

`LENGTH`, `BATCH`, and `CHUNK` must be integer constant expressions (no VLA).

**What gets generated:**

- On AVX-512: fills a stack-resident scratch buffer (L1-resident, `LENGTH + CHUNK`
  samples), calls `fn##_step_batch()` in strides of `BATCH`, then falls
  through to the scalar tail via `fn##_step()`.
- Everywhere else: loops directly over `fn##_step()`.

The three constants are the only coupling between layers.  You write `step()`.
You optionally write `step_batch()` for SIMD.  `JM_DEFINE_STEPS` owns the rest.

Convention: `state->delay[0..LENGTH-1]` is the delay line, `delay[0]` = newest.

---

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

`FIR_LENGTH` is what the macro sees — the history depth.  `FIR_TAPS` is the
FIR-specific concept; `step_batch()` loops over it, and the window index
`FIR_LENGTH - k` reaches back exactly `TAPS` samples (index 0 = oldest
history, index `FIR_LENGTH` = current sample).

**In the component source** — tune the chunk size and generate `steps()`:

```c
#define FIR_CHUNK 256  /* tuning: samples per scratch-buffer fill */

JM_DEFINE_STEPS(fir_filter, fir_filter_state_t, float complex,
                FIR_LENGTH, FIR_BATCH, FIR_CHUNK)
```

See the [FIR filter example](../examples/fir_filter/README.md) for a complete
walkthrough including benchmarks.

---

## `jm_simd.h` — width-portable operation macros

Raw AVX intrinsics lock your `step_batch()` to one ISA.  `jm_simd.h` provides
macros that select the widest available instruction set at compile time —
AVX-512, AVX2+FMA, or scalar — so the same source compiles everywhere.

Included automatically by `jm_perf.h`; can also be included standalone.

### SIMD tier selection

| Tier | `JM_SIMD_WIDTH_F32` | `JM_VEC_F32` | `JM_VEC_F64` |
|---|---|---|---|
| AVX-512F | 16 | `__m512` | `__m512d` |
| AVX2 + FMA | 8 | `__m256` | `__m256d` |
| Scalar | 1 | `float` | `double` |

`JM_SIMD_WIDTH_F64` is always half of `JM_SIMD_WIDTH_F32`.

### Operation macros

| Macro | Operation |
|---|---|
| `JM_ZERO_F32()` / `JM_ZERO_F64()` | Zero accumulator |
| `JM_SPLAT_F32(x)` / `JM_SPLAT_F64(x)` | Broadcast scalar to all lanes |
| `JM_LOAD_F32(ptr)` / `JM_LOAD_F64(ptr)` | Unaligned load |
| `JM_STORE_F32(ptr, v)` / `JM_STORE_F64(ptr, v)` | Store |
| `JM_ADD_F32(a, b)` / `JM_ADD_F64(a, b)` | Element-wise add |
| `JM_MUL_F32(a, b)` / `JM_MUL_F64(a, b)` | Element-wise multiply |
| `JM_FMA_F32(acc, a, b)` / `JM_FMA_F64(...)` | `acc += a * b` |
| `JM_MAC_F32(acc, ptr, s)` / `JM_MAC_F64(...)` | Load `JM_SIMD_WIDTH_F32` floats from `ptr`, multiply by scalar `s`, accumulate |
| `JM_HSUM_F32(v)` / `JM_HSUM_F64(v)` | Horizontal reduce all lanes to one scalar |

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

On AVX-512 this expands to `_mm512_fmadd_ps` + `_mm512_reduce_add_ps`.
On scalar it compiles to a plain loop the compiler can auto-vectorise.
No `#ifdef` in user code; the tier is chosen once at the top of `jm_simd.h`.

### Additional hint macros (in `jm_perf.h`)

| Macro | Effect |
|---|---|
| `JM_UNROLL(n)` | `#pragma GCC unroll n` — ask compiler to unroll loop `n` times |
| `JM_ASSUME_ALIGNED(ptr, n)` | `__builtin_assume_aligned` — enables SIMD loads without alignment penalty |
| `JM_PREFETCH(ptr, rw, loc)` | `__builtin_prefetch` — software prefetch; `rw`=0 read / 1 write, `loc`=0–3 |

---

## SIMD build flag

SIMD intrinsics require `-march=native -ffast-math`.  Pass `-DENABLE_SIMD=ON`
to CMake:

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON
cmake --build build --parallel
```
