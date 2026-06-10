## 2. Implement the producer

A `--variable-output` method generates two stubs in
`native/src/drainer/drainer_core.c`: `drainer_run_max_out()` (the upper bound
on output size) and `drainer_run()` (the producer itself). Fill them in.

The bound — one call can at most return the whole remaining source:

```{02_max_out.c}
```

The producer — emit up to `n` samples, advance `pos`, and return the count:

```{02_run.c}
```

That is all the C. The output buffer, the zero-copy numpy view, and the
`stream()` / `__iter__` iterator are generated around these two functions.
(Both are spliced into the build and run by the example's test, so what you
read here is exactly what compiles.)
