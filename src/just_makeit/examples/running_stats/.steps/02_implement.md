## 2. Implement

Open `native/inc/running_stats/running_stats_core.h` and replace the stub.
The algorithm mutates state, so the signature changes from `const` to mutable.
The real part of the input is the sample value; the return packs `mean` into
the real part and sample variance into the imaginary part:

```{02_step_before.c}
```

```{02_base_step.c}
```
