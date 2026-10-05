## 2. `method` — scalar stub + `--batch` companion

Use `just-makeit method` when you need an execute path with **different
input or output types** than the primary `step()`.

```{02_method_scalar.sh}
```

The command declares it in `native/inc/my_arrays/ema/ema_core.h` and appends a
stub to `native/src/ema/ema_core.c`:

```c
uint32_t my_arrays_ema_quantize(my_arrays_ema_state_t *state, float x);
```

For **1:1-rate batch work** (output count equals input count), declare a batch
method and let jm generate its binding. `native/src/ema/ema_ext.c` is jm's
glue, rewritten by every `jm apply`, so never edit it by hand:

```sh
just-makeit method ema quantize_steps \
    --arg-type float \
    --return-type uint32_t \
    --batch
```

That declares
`void my_arrays_ema_quantize_steps(my_arrays_ema_state_t *state, const float *in, size_t n, uint32_t *out);`
and appends its stub to `native/src/ema/ema_core.c`. Implement it as a loop
over the scalar method:

```{02_method_scalar_batch.c}
```

### Array ownership for a `--batch` method

The Python caller's experience is identical to the auto-generated `steps()`:
pass one input array, get back a new numpy array.

```
call f.quantize_steps(block)
│
├─ ext calls PyArray_SimpleNew(n, uint32)   ← one malloc, every call
│
├─ calls my_arrays_ema_quantize_steps(state, block.data, n, out.data)
│    └─ loop: out[i] = my_arrays_ema_quantize(state, block[i])
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — object holds no reference to it
```

The C function `my_arrays_ema_quantize_steps` takes both pointers, but the ext
owns that allocation. As with `steps()`, a caller that wants to reuse a buffer
passes it as `out=` (`f.quantize_steps(block, buf)`) and the ext writes into it
instead.

**When to use this pattern**

- You need a different input or output type than the primary `step()`.
- Output count equals input count (1:1 rate).
- jm generates the binding, `out=` included; you write only the loop.

**When not to use it**

If the output count differs from the input count (e.g. a decimator), use
`--variable-output`. See §3.
