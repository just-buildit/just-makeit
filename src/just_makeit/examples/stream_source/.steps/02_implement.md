## 2. Implement `step()`

A source's whole algorithm lives in the inline `step()` in
`native/inc/ramp/ramp_core.h`. Replace the generated stub with the ramp
recurrence — emit the current value, then advance it:

```{02_step.c}
```

That is the only C you write. `steps(n)` (the per-sample loop) and the entire
`stream()` / `__iter__` machinery are generated around it — they call this
`step()` for you. (This very function is spliced into the build and run by the
example's test, so what you read here is exactly what compiles.)
