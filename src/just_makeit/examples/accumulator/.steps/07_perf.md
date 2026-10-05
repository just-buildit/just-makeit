## 7. Bonus: `just-makeit perf` + explicit SIMD

### 7.1 Enable perf infrastructure

```{07_scaffold.sh}
```

One command adds three things to the project:

| Added | Effect |
| ----- | ------ |
| `native/inc/my_acc/jm_perf.h` | `JM_FORCEINLINE`, `JM_HOT`, `JM_RESTRICT`, `JM_LIKELY`, ... |
| `native/inc/my_acc/jm_simd.h` | `JM_VEC_F32`, `JM_ADD_F32`, `JM_LOAD_F32`, `JM_HSUM_F32`, `JM_SIMD_WIDTH_F32` |
| `#include "my_acc/jm_perf.h"` inserted in each `_core.h` | Makes all macros available to every `.c` that includes the header |

`just-makeit.toml` gains `perf = "true"` and each object's `step()` qualifier is
upgraded from `static inline` to `JM_FORCEINLINE JM_HOT`.

### 7.2 Benchmark

Save this as `bench.py` in `my_acc/`:

```{07_bench.py}
```

Build and measure across three stages (stage 3 runs the patch script from
this example's `.steps/` directory in the installed package, the same
`$STEPS` as section 4):

```{07_bench.sh}
```

### 7.3 Results

Measured on x86-64 (AVX-512), AMD Ryzen AI 9 465, `BLOCK = 100_000`; the
script's output with the CMake build lines omitted:

```
=== baseline (scalar) ===
AccF32  100,000 samples  1.43 G samples/sec
AccCf64 100,000 samples  1.42 G samples/sec
=== ENABLE_SIMD=ON (auto-vectorised) ===
AccF32  100,000 samples  1.43 G samples/sec
AccCf64 100,000 samples  1.42 G samples/sec
patched native/src/acc_f32/acc_f32_core.c
=== explicit SIMD (JM_ADD_F32 + JM_HSUM_F32) ===
AccF32  100,000 samples  22.46 G samples/sec
AccCf64 100,000 samples  1.42 G samples/sec
```

Stage 3 runs `AccF32.steps()` about 16x faster than stage 1; stage 2 moves
nothing.

### 7.4 Why stage 2 does not improve

Stage 1 to stage 2 adds `-ffast-math` and `-march=native`.  `-ffast-math` allows
the compiler to reassociate the reduction — a prerequisite for vectorisation.
So why does stage 2 show no gain?

The generated `my_acc_acc_f32_steps()` signature is:

```c
void my_acc_acc_f32_steps(
    my_acc_acc_f32_state_t *state,
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

```sh
python3 "$STEPS/07_patch_perf.py"   # stage 3 above already ran it
```

The replacement in `native/src/acc_f32/acc_f32_core.c`:

```c
#if JM_SIMD_WIDTH_F32 > 1
JM_HOT void
my_acc_acc_f32_steps(my_acc_acc_f32_state_t *JM_RESTRICT state,
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
my_acc_acc_f32_steps(my_acc_acc_f32_state_t *JM_RESTRICT state,
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
| AArch64 NEON | 4                | `float32x4_t` | `vaddq_f32`  |
| Scalar    | 1                   | `float`      | `+`          |

The same macros compile to the widest tier the target and compiler flags
enable; on x86-64 the AVX tiers need `ENABLE_SIMD=ON` (`-march=native`), while
NEON is always on for AArch64. With the `#if JM_SIMD_WIDTH_F32 > 1` guard the
vector loop is compiled only where a SIMD tier exists; on scalar targets the
`#else` branch compiles instead, keeping the generated `.so` valid on a target
with no SIMD support.

### 7.7 `AccCf64` and complex SIMD

The `AccCf64` lines show no improvement in any stage because the same
aliasing problem applies to `my_acc_acc_cf64_steps()` and the patch only
covers `acc_f32`.  Adding `JM_RESTRICT` there follows the same pattern.
Explicit SIMD for `double _Complex` is more involved: the storage is two
consecutive doubles (real then imaginary), so you need `JM_VEC_F64` with
stride-2 access or interleaved accumulation — left as an exercise once the
`AccF32` workflow is understood.
