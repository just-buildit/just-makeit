## 1. Scaffold

```{01_scaffold.sh}
```

Three state variables — all zero by default, so `RunningStats()` needs no arguments:

| Name   | Type       | Role                              |
|--------|------------|-----------------------------------|
| `n`    | `int32_t`  | Sample count                      |
| `mean` | `double`   | Running mean (Welford)            |
| `m2`   | `double`   | Sum of squared deviations (Welford) |

Variance = `m2 / (n - 1)` once `n > 1`.
