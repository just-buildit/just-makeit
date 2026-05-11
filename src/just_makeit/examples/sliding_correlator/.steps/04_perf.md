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
needs.  `CORR_TAPS` and `CORR_BATCH` are algorithm-specific; the macro never
sees them directly.

`step_batch()` computes the same dot product as `step()` but reads from a
pre-built contiguous window instead of the delay line, so the inner loop has
no scatter-gather and the compiler can auto-vectorise it freely.  No `#ifdef`
guard is needed: `_JM_STEPS_SIMD_` only calls `step_batch()` when
`JM_SIMD_WIDTH_F32 > 1`, so scalar builds simply use the `step()` path.

Replace `sliding_correlator_steps` in `native/src/sliding_correlator/sliding_correlator_core.c`:

```{04_kernel.c}
```

`JM_DEFINE_STEPS` generates `sliding_correlator_steps()` — scratch buffer,
chunked fill, SIMD dispatch (AVX-512 or AVX2), scalar tail.  The three
constants keep each concern separate:

| constant      | concern      | meaning                                             |
|---------------|--------------|-----------------------------------------------------|
| `CORR_LENGTH` | algorithm    | history depth (`delay[]` entries)                   |
| `CORR_BATCH`  | parallelism  | complex samples per call (`JM_SIMD_WIDTH_F32 / 2`)  |
| `CORR_CHUNK`  | tuning       | samples per scratch-buffer fill                     |
