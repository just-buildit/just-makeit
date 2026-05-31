## 6. Add more state

Track the min and max alongside the running statistics:

```{06_add_state.sh}
```

State is *structural*: `add` rewrites the `running_stats_state_t` struct and
the `create()` / `reset()` lifecycle, so it rebuilds the object from the
manifest rather than splicing into your sources. That rebuild resets
`running_stats_step()` back to a fresh stub, so re-run the implement step to
restore the algorithm — now on top of the new `min_val` / `max_val` fields:

```{02_step_after.c}
```
