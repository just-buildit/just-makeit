## 2. Implement the producer

A `--variable-output` method generates two stubs in
`native/src/drainer/drainer_core.c`: `drainer_run_max_out()` (the upper bound
on output size) and `drainer_run()` (the producer itself). Fill them in.

The bound — one call can at most return the whole remaining source. The
binding uses it to size the reusable output buffer once, at `__init__`:

```c
size_t
drainer_run_max_out(drainer_state_t *state)
{
    return (size_t)state->total;
}
```

The producer — emit up to `n` samples as an ascending complex ramp, advance
`pos`, and return the count. The empty return once drained (`pos == total`) is
what makes `stream()` terminate:

```c
size_t
drainer_run(drainer_state_t *state, size_t n, float complex *out)
{
    int32_t avail = state->total - state->pos;
    if (avail < 0)
        avail = 0;
    size_t k = (size_t)avail < n ? (size_t)avail : n;
    for (size_t i = 0; i < k; i++)
        out[i] = (float complex)(float)(state->pos + (int32_t)i);
    state->pos += (int32_t)k;
    return k;
}
```

That is all the C. The output buffer, the zero-copy numpy view, and the
`stream()` / `__iter__` iterator are generated around these two functions.
