## 7. Bonus: `--perf` + SIMD benchmark

From inside `my_fir`:

```{07_scaffold.sh}
```

Save the benchmark below as `bench.py`:

```{07_bench.py}
```

Build baseline, measure, rebuild with SIMD, measure again:

```{07_bench.sh}
```

The numbers below were measured on one AVX-512 machine (`bench.py` reports
the best of five timed repeats). Yours will differ; the ratios are what
carries over.

### Round 1 — flags alone

The section 2 kernel shifts the delay line with `memmove`.
Adding `-march=native -ffast-math` via `ENABLE_SIMD=ON` gives a modest gain:

```
baseline:  65.1 M complex samples/sec
with SIMD: 90.6 M complex samples/sec   (1.4×)
```

The ceiling is the `memmove` of 120 bytes (15 `float _Complex`) that runs every
sample.  The vectoriser can auto-vectorise the 16-tap MAC, but it can't overlap
that store with the accumulate.  Flags alone don't get you there.

### Round 2 — algorithm matters

Three concerns, three places.  `jm_perf.h` ships a `JM_DEFINE_STEPS` macro
that stamps out the outer dispatch loop so you never write it by hand.

**1.** Add the constants and `my_fir_fir_filter_step_batch()` to
`native/inc/my_fir/fir_filter/fir_filter_core.h` just after `my_fir_fir_filter_step()`:

```{07_step_batch.h}
```

Three named constants make each concern explicit:

| constant    | concern     | meaning                                            |
| ----------- | ----------- | -------------------------------------------------- |
| `FIR_TAPS`  | algorithm   | filter length (a compile-time constant you define) |
| `FIR_BATCH` | parallelism | complex samples per call (`JM_SIMD_WIDTH_F32 / 2`) |
| `FIR_CHUNK` | tuning      | samples per scratch-buffer fill                    |

`FIR_BATCH` is derived from `JM_SIMD_WIDTH_F32` (16 on AVX-512, 8 on AVX2,
4 on AArch64 NEON), so the same source compiles to 8, 4 or 2 complex samples
per batch without any `#ifdef`.  On scalar targets `JM_SIMD_WIDTH_F32 = 1`,
`JM_STEPS_SIMD_IMPL` is a no-op, and `step_batch()` is never called.

`step_batch()` uses `FIR_TAPS` and `FIR_BATCH`.  `steps()` uses all three —
but you never write `steps()`.

**2.** Replace `my_fir_fir_filter_steps` in `native/src/fir_filter/fir_filter_core.c`:

```{07_kernel.c}
```

`JM_DEFINE_STEPS` generates `my_fir_fir_filter_steps()` from the macro in `jm_perf.h`:
it owns the scratch buffer, the chunked fill, and the scalar tail.  You write
`step()`.  You write `step_batch()`.  The rest is infrastructure.

```
baseline:    64.7 M complex samples/sec   (unchanged: the batch path is compiled out)
with SIMD: 1315.1 M complex samples/sec   (20× the baseline)
```

The baseline does not move.  Without `ENABLE_SIMD=ON` an x86-64 build has no
AVX tier, so `JM_SIMD_WIDTH_F32` is 1, `JM_STEPS_SIMD_IMPL` expands to
nothing, and `steps()` is the same scalar loop over the `memmove` `step()`
as round 1.  With `ENABLE_SIMD=ON` the scratch-buffer path runs:
`step_batch()` handles `FIR_BATCH` complex samples per call (8 on AVX-512,
4 on AVX2) over an L1-resident chunk, with no per-sample `memmove`.
`jm_simd.h` selects the tier at compile time, no source changes needed.  On
AArch64, NEON is always available, so even the baseline build takes the
batch path there (2 complex samples per call).
