## 2. Implement gain

Open `native/inc/gain/gain_core.h` and replace the `gain_step` stub — it's a one-liner:

```{02_step_after.c}
```

`gain_steps()` in `gain_core.c` loops over this automatically — no changes needed there.
