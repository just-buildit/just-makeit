## 7. Bonus: `--perf` + SIMD benchmark

`--perf` rewrites the scaffold to annotate `fir_filter_step` with
`JM_FORCEINLINE JM_HOT` and generates `jm_perf.h`.

Scaffold a perf-enabled copy from the parent directory of `my_fir`:

```{07_scaffold.sh}
```

Implement `fir_filter_step` exactly as in step 2 — the logic is unchanged,
only the qualifier differs.  Then save the benchmark below as `bench.py`:

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

Replace the per-sample `memmove` with a **scratch-buffer kernel**:

1. Copy `state->delay[0..14]` followed by `input[0..N-1]` into a flat scratch
   array (processed in chunks so the hot window fits in L1 cache).
2. Run an AVX-512 inner loop: for each tap `k`, broadcast `h[k]` and `fmadd`
   against 8 consecutive interleaved complex samples — **1 FMA per tap per 8
   outputs**, no permute, no modulo arithmetic.
3. Copy `scratch[N..N+14]` back to `state->delay`.

```c
/* hot kernel — explicit AVX-512 */
for (i = 0; i + 8 <= n; i += 8) {
    __m512 acc = _mm512_setzero_ps();
    for (k = 0; k < 16; k++) {
        __m512 vx = _mm512_loadu_ps((float *)(scratch + i + 15 - k));
        acc = _mm512_fmadd_ps(_mm512_set1_ps(h[k]), vx, acc);
    }
    _mm512_storeu_ps((float *)(out + i), _mm512_mul_ps(acc, vg));
}
```

```
baseline:   475 M complex samples/sec
with SIMD: 1745 M complex samples/sec   (3.7×)
```

The scalar baseline is already 4.5× faster than the `memmove` version because
sequential scratch accesses are hardware-prefetcher-friendly; the L1-resident
chunk eliminates the circular-buffer index arithmetic entirely.  Adding
`ENABLE_SIMD=ON` then delivers the full 3.7× from AVX-512's 16-wide float FMA.
