## 6. Add more state

```{06_add_state.sh}
```

State is structural, so `add` rebuilds the object from the manifest: the
`fir_filter_state_t` struct and lifecycle are regenerated and your
`fir_filter_step()` body is reset to a fresh stub. Re-run the implement step
(section 2) to restore the kernel on top of the new state. The same applies
when you swap in a longer delay line:

```{06_add_coeffs64.sh}
```
