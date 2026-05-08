# sliding_power — sliding window signal power estimator

Estimate the instantaneous power of a signal over a rolling window of N samples:

```
P[n] = (1/N) * sum( |x[n-k]|^2  for k in 0..N-1 )
```

Two update strategies are shown:

- **step()** — O(1) recursive: `sum_sq += new² − old²`
- **SIMD recompute** — horizontally sums the delay line with `JM_ADD_F32` /
  `JM_HSUM_F32` from `jm_simd.h`; used for periodic recalibration and as a
  clean demonstration of the v0.5 macro set.
