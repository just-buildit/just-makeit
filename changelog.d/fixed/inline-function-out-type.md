- **`jm function --inline` takes every shape the out-of-line stub does.**
    The header stub for an inline module function rendered its own
    signature and knew only scalars, so `--inline --out-type` scaffolded
    `f(const T *x, size_t x_len)` while the binding called `f(x, x_len, out)`
    and the project did not compile (the feature tour's Step 6). It also
    dropped an `--impl` body, leaving the placeholder. The inline stub is
    now the out-of-line stub with a `static inline` storage class.
