## 2. Implement the producer

A `--variable-output` method generates two stubs in
`native/src/drainer/drainer_core.c`: `stream_blockwise_demo_drainer_run_max_out()` (the upper bound
on output size) and `stream_blockwise_demo_drainer_run()` (the producer itself). Fill them in.

The bound — one call can at most return the whole remaining source:

```{02_max_out.c}
```

The producer — emit up to `n` samples, advance `pos`, and return the count:

```{02_run.c}
```

That is all the C. The NumPy-owned array each `run()` returns and the
`stream()` / `__iter__` iterator are generated around these two functions.
(Both are spliced into the build and run by the example's test, so what you
read here is exactly what compiles.)

One more edit gives the generated class a real docstring instead of the
generic `Drainer component.` fallback. In the sacred
`native/inc/stream_blockwise_demo/drainer/drainer_core.h`, replace the
scaffold's `@brief Create a drainer instance.` above
`stream_blockwise_demo_drainer_create()` with your own sentence, then
re-derive the stub from it:

```sh
just-makeit apply      # drainer.pyi's class docstring now reads your @brief
```
