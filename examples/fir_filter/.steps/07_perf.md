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

### Round 1 — flags alone

The scaffold's default implementation shifts the delay line with `memmove`.
Adding `-march=native -ffast-math` via `ENABLE_SIMD=ON` gives a modest gain:

```
baseline:  106.8 M complex samples/sec
with SIMD: 154.1 M complex samples/sec   (1.4×)
```

The ceiling is the `memmove` of 120 bytes (15 `float complex`) that runs every
sample.  The vectoriser can auto-vectorise the 16-tap MAC, but it can't overlap
that store with the accumulate.  Flags alone don't get you there.

### Round 2 — algorithm matters

Three concerns, three places.  `jm_perf.h` ships a `JM_DEFINE_STEPS` macro
that stamps out the outer dispatch loop so you never write it by hand.

**1.** Add the constants and `fir_filter_step_batch()` to
`native/inc/fir_filter/fir_filter_core.h` just after `fir_filter_step()`:

```{07_step_batch.h}
```

Three named constants make each concern explicit:

| constant    | concern      | meaning                              |
|-------------|--------------|--------------------------------------|
| `FIR_TAPS`  | algorithm    | filter length (set at codegen time)  |
| `FIR_BATCH` | parallelism  | AVX-512 complex samples per call     |
| `FIR_CHUNK` | tuning       | samples per scratch-buffer fill      |

`step_batch()` uses `FIR_TAPS` and `FIR_BATCH`.  `steps()` uses all three —
but you never write `steps()`.

**2.** Replace `fir_filter_steps` in `native/src/fir_filter/fir_filter_core.c`:

```{07_kernel.c}
```

`JM_DEFINE_STEPS` generates `fir_filter_steps()` from the macro in `jm_perf.h`:
it owns the scratch buffer, the chunked fill, and the scalar tail.  You write
`step()`.  You write `step_batch()`.  The rest is infrastructure.

```
baseline:   475 M complex samples/sec
with SIMD: 1745 M complex samples/sec   (3.7×)
```

The scalar baseline is already 4.5× faster than the `memmove` version because
sequential scratch accesses are hardware-prefetcher-friendly; the L1-resident
chunk eliminates the circular-buffer index arithmetic entirely.  Adding
`ENABLE_SIMD=ON` then delivers the full 3.7× from AVX-512's 16-wide float FMA.
