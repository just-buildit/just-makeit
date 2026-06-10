## 2. Implement `step()`

The algorithm is unchanged from the sync example — async iteration reuses the
exact same producer. Replace the inline `step()` stub in
`native/inc/ramp/ramp_core.h` with the ramp recurrence:

```{02_step.c}
```

That is the only C you write. `steps(n)`, the sync `stream()` / `__iter__`, and
the async `__aiter__` / `__anext__` are all generated around this one `step()`
— `__anext__` just calls it from the event loop's executor.
