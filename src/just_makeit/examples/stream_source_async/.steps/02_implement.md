## 2. Implement `step()`

The algorithm is unchanged from the sync example — async iteration reuses the
exact same producer. Replace the inline `step()` stub in
`native/inc/stream_source_async_demo/ramp/ramp_core.h` with the ramp recurrence:

```{02_step.c}
```

That is the only C you write. `steps(n)`, the sync `stream()` / `__iter__`, and
the async `__aiter__` / `__anext__` are all generated around this one `step()`
— `__anext__` just calls it from the event loop's executor.

One more edit gives the generated class a real docstring instead of the
generic `Ramp component.` fallback. In the same header, replace the
scaffold's `@brief Create a ramp instance.` above
`stream_source_async_demo_ramp_create()` with your own sentence, then re-derive the
stub from it:

```sh
just-makeit apply      # ramp.pyi's class docstring now reads your @brief
```
