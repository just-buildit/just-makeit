## 5. SIMD recompute with `jm_simd.h`

The recursive `step()` accumulates `sum_sq` incrementally — fast, but
floating-point rounding errors accumulate over millions of samples.  A
periodic full recompute from the delay line corrects any drift.

Add this function to `native/src/power_est/power_est_core.c` (it needs
`jm_simd.h`, included via `jm_perf.h`):

```{05_simd_recompute.c}
```

**What each macro does on each ISA:**

| Macro             | AVX-512                   | AVX2                 | Scalar           |
| ----------------- | ------------------------- | -------------------- | ---------------- |
| `JM_VEC_F32`      | `__m512` (16 lanes)       | `__m256` (8 lanes)   | `float` (1 lane) |
| `JM_LOAD_F32(p)`  | `_mm512_loadu_ps(p)`      | `_mm256_loadu_ps(p)` | `*(p)`           |
| `JM_ADD_F32(a,b)` | `_mm512_add_ps(a,b)`      | `_mm256_add_ps(a,b)` | `(a)+(b)`        |
| `JM_HSUM_F32(v)`  | `_mm512_reduce_add_ps(v)` | `_mm_hadd_ps(...)`   | `(v)`            |
| `JM_UNROLL(4)`    | `#pragma GCC unroll 4`    | same                 | same             |

The loop body is identical across all three tiers.  `JM_SIMD_WIDTH_F32`
(16, 8, or 1) controls the stride; the 64-element delay line is always
an exact multiple of any supported width, so there is no scalar tail.

Build with `-DENABLE_SIMD=ON` to activate AVX-512 or AVX2 paths:

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON
cmake --build build --parallel
```
