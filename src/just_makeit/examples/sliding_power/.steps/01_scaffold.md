## 1. Scaffold

The state has three fields: a 64-element float ring buffer (`delay`) that
stores |x|² for the last 64 samples, a running double accumulator (`sum_sq`),
and a write-head index (`pos`).  `--perf` generates `jm_perf.h` and
`jm_simd.h` alongside the scaffold.

```{01_scaffold.sh}
```
