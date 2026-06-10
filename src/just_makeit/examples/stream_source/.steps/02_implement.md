## 2. Implement `step()`

A source's whole algorithm lives in the inline `step()` in
`native/inc/ramp/ramp_core.h`. Replace the generated stub body with the ramp
recurrence — emit the current value, then advance it:

```c
static inline float
ramp_step(ramp_state_t *state)
{
    /* `value` and `step_inc` are state fields, so each call resumes where the
       last one left off — exactly what stream() drives, block by block. */
    const float out = state->value;
    state->value += state->step_inc;
    return out;
}
```

That is the only C you write. `steps(n)` (the per-sample loop) and the entire
`stream()` / `__iter__` machinery are generated around it — they call this
`step()` for you.
