## 2. Implement

Open `native/inc/fir_filter/fir_filter_core.h` and replace the `fir_filter_step` stub.
The filter must update the delay line, so the signature changes from `const` to mutable:

```{02_step_before.c}
```

```{02_step_after.c}
```

`fir_filter_steps()` in `fir_filter_core.c` loops over this automatically —
no changes needed there.
