## 2. Implement step()

Replace the generated stub in `native/inc/power_est/power_est_core.h` with
the recursive O(1) update.  The delay line stores `|x|²` for each past sample;
`sum_sq` is the running total.

```{02_step_impl.c}
```

Apply the patch:

```sh
python3 .steps/02_patch.py
```
