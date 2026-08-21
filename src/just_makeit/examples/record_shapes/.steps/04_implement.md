## 4. Implement the kernels

```{04_patch.py}
```

Three bodies, three different contracts, and none of them is guessable from
`result_fields` alone:

- `summary()` builds an `evlog_summary_t` on the stack and **returns it**.
- `read()` writes whole `evlog_rec_t` values into the `out` buffer the
    binding sized for it, and returns how many are valid. Its
    `read_max_out()` companion is what the binding calls first to do that
    sizing.
- `peaks()` writes whole `evlog_peak_t` rows into `result[]`, capped at
    `max_results`, and returns how many. jm reads the members named by
    `result_fields` off each row and builds a tuple from them.
