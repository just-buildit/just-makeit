## 7. Bonus: `just-makeit perf` + explicit SIMD

### 7.1 Enable perf infrastructure

```{07_scaffold.sh}
```

One command adds three things to the project:

| Added | Effect |
| ----- | ------ |
| `native/inc/jm_perf.h` | `JM_FORCEINLINE`, `JM_HOT`, `JM_RESTRICT`, `JM_LIKELY`, ... |
| `native/inc/jm_simd.h` | `JM_VEC_F32`, `JM_ADD_F32`, `JM_LOAD_F32`, `JM_HSUM_F32`, `JM_SIMD_WIDTH_F32` |
| `#include "jm_perf.h"` inserted in each `_core.h` | Makes all macros available to every `.c` that includes the header |

`just-makeit.toml` gains `perf = "true"` and each object's `step()` qualifier is
upgraded from `static inline` to `JM_FORCEINLINE JM_HOT`.

### 7.2 Benchmark

Save this as `bench.py` in `my_acc/`:

```{07_bench.py}
```

Build and measure across three stages:

```{07_bench.sh}
```

### 7.3 Results

Measured on x86-64 (AVX2), AMD Ryzen 9, `BLOCK = 100_000`:

```
=== Stage 1: Release -O3, JM_FORCEINLINE JM_HOT on step(), scalar steps() ===
AccF32  100,000 samples  1.66 G samples/sec   1.0×

=== Stage 2: ENABLE_SIMD=ON (-ffast-math -march=native) ===
AccF32  100,000 samples  1.65 G samples/sec   ≈1.0×

=== Stage 3: explicit JM_VEC_F32 + JM_RESTRICT, ENABLE_SIMD=ON ===
AccF32  100,000 samples  18.11 G samples/sec  10.9×
```

### 7.4 Why stage 2 does not improve

Stage 1 to stage 2 adds `-ffast-math` and `-march=native`.  `-ffast-math` allows
the compiler to reassociate the reduction — a prerequisite for vectorisation.
So why does stage 2 show no gain?

The generated `acc_f32_steps()` signature is:

```c
void acc_f32_steps(
    acc_f32_state_t *state,
    const float     *input,
    size_t           n)
```

Both `state` (which contains `state->acc`, a `float`) and `input` are `float`
pointers.  Without a `restrict` qualifier, the compiler must assume they could
overlap — that `input[i]` might be the same memory as `state->acc`.  Under that
assumption every iteration must observe the previous one's store before it can
load `input[i]`, serialising the entire loop regardless of flags.

### 7.5 The unlock: `JM_RESTRICT`

The patch script (`07_patch_perf.py`) replaces the generated `steps()` with an
explicit SIMD version.  Two things happen simultaneously:

1. `JM_RESTRICT` is added to both parameters — telling the compiler they
   cannot alias.  Now it is free to vectorise or reorder reads.
2. The inner loop is written explicitly using `JM_VEC_F32`, `JM_ADD_F32`,
   `JM_LOAD_F32`, and `JM_HSUM_F32`.

```python
python3 .steps/07_patch_perf.py
```

The replacement in `native/src/acc_f32/acc_f32_core.c`:

```c
#if JM_SIMD_WIDTH_F32 > 1
JM_HOT void
acc_f32_steps(acc_f32_state_t *JM_RESTRICT state,
              const float *JM_RESTRICT input, size_t n)
{
    JM_VEC_F32 vacc = JM_ZERO_F32();
    size_t i = 0;
    for (; i + JM_SIMD_WIDTH_F32 <= n; i += JM_SIMD_WIDTH_F32)
        vacc = JM_ADD_F32(vacc, JM_LOAD_F32(input + i));
    state->acc += JM_HSUM_F32(vacc);
    for (; i < n; i++)
        state->acc += input[i];
}
#else
JM_HOT void
acc_f32_steps(acc_f32_state_t *JM_RESTRICT state,
              const float *JM_RESTRICT input, size_t n)
{
    for (size_t i = 0; i < n; i++)
        state->acc += input[i];
}
#endif
```

### 7.6 Width portability

`JM_SIMD_WIDTH_F32` is set at compile time by `jm_simd.h`:

| ISA       | `JM_SIMD_WIDTH_F32` | `JM_VEC_F32` | `JM_ADD_F32` |
| --------- | ------------------- | ------------ | ------------ |
| AVX-512F  | 16                  | `__m512`     | `_mm512_add_ps` |
| AVX2+FMA  | 8                   | `__m256`     | `_mm256_add_ps` |
| Scalar    | 1                   | `float`      | `+`          |

The same source compiles to the widest available tier with no `#ifdef` in user
code.  On scalar targets `JM_SIMD_WIDTH_F32 == 1` so `i + 1 <= n` is always
true in the vector loop — it degenerates to a single-element loop identical to
the `#else` branch, and `JM_HSUM_F32` is a no-op identity.

The `#if / #else / #endif` guard is a safety net: on scalar targets the
`#else` branch compiles instead, keeping the generated `.so` valid even on a
machine with no SIMD support.

### 7.7 `AccCf64` and complex SIMD

The `AccCf64` benchmarks show no improvement because the same aliasing problem
applies to `acc_cf64_steps()` and the patch only covers `acc_f32`.  Adding
`JM_RESTRICT` there follows the same pattern.  Explicit SIMD for `double
_Complex` is more involved: the storage is two consecutive doubles (real then
imaginary), so you need `JM_VEC_F64` with stride-2 access or interleaved
accumulation — left as an exercise once the `AccF32` workflow is understood.
