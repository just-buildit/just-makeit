## 8. Pure variant — caller-managed params

The stateful FIR (steps 1–7) uses an opaque `fir_filter_state_t *` that
just-makeit allocates and owns.  For the **pure** variant, the caller owns the
`params_t` struct directly — multiple channels can share the same algorithm
without any allocation inside the hot path.

### Scaffold

```{08_pure.sh}
```

`--pure` with array state (`float[16]`) auto-selects the **struct** style.
just-makeit generates:

| Generated | What it is |
|-----------|-----------|
| `fir_pure_params_t` | Non-opaque struct; layout is public |
| `fir_pure_params_create()` | Heap alloc + zero-init (`calloc`; comment shows `aligned_alloc` for SIMD) |
| `fir_pure_params_free()` | Free; safe to call with NULL |
| `fir_pure_params_init()` | In-place zero-init for stack / custom allocation |
| `fir_pure_fn(x, *p)` | Single-sample transform — `p` is modified in place |
| `fir_pure_steps(in, out, n, *p)` | Block transform |
| `FirPure` Python class | `obj(x)` calls `tp_call`; `obj.steps(arr)` calls batch path |

Because the struct is non-opaque, a caller can stack-allocate, pool-allocate,
or use `aligned_alloc` for SIMD — all without any support from the library.

### Implement

Replace the `fir_pure_fn` stub in `native/inc/fir_pure/fir_pure_core.h`:

```{08_fn_impl.c}
```

The logic is identical to the stateful version; the only difference is the
parameter type: `fir_pure_params_t *` instead of `fir_filter_state_t *`.

### Python demo

```{08_demo.py}
```

Key differences from the stateful version:

| | Stateful (`FirFilter`) | Pure (`FirPure`) |
|--|--|--|
| Allocation | `fir_filter_create()` inside Python | `fir_pure_params_create()` inside Python |
| Process one sample | `obj.step(x)` | `obj(x)` — instance is callable |
| Multiple channels | Separate instances, separate heap allocs | Separate `FirPure` instances; params struct may be stack-allocated in C |
| State ownership | Library | Caller |
