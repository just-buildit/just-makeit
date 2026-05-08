## 4. `JM_DEFINE_STEPS`

From inside `my_corr`:

```{04_scaffold.sh}
```

Add `CORR_TAPS`, `CORR_LENGTH`, `CORR_BATCH`, and `sliding_correlator_step_batch()`
to `native/inc/sliding_correlator/sliding_correlator_core.h` just after
`sliding_correlator_step()`:

```{04_step_batch.h}
```

`CORR_LENGTH = CORR_TAPS - 1` is the history depth — the quantity `JM_DEFINE_STEPS`
needs.  `CORR_TAPS` and `CORR_BATCH` are FIR-specific; the macro never sees them.

`step_batch()` computes the same dot product as `step()` but reads from a
pre-built contiguous window instead of the delay line, so the inner loop has
no scatter-gather and the compiler can vectorise it freely.

Replace `sliding_correlator_steps` in `native/src/sliding_correlator/sliding_correlator_core.c`:

```{04_kernel.c}
```

`JM_DEFINE_STEPS` generates `sliding_correlator_steps()` — scratch buffer,
chunked fill, AVX-512 dispatch, scalar tail.  The three constants keep each
concern separate:

| constant      | concern      | meaning                             |
|---------------|--------------|-------------------------------------|
| `CORR_LENGTH` | algorithm    | history depth (`delay[]` entries)   |
| `CORR_BATCH`  | parallelism  | samples per `step_batch()` call     |
| `CORR_CHUNK`  | tuning       | samples per scratch-buffer fill     |
