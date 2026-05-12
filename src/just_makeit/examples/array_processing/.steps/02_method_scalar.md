## 2. `method` — scalar stub + hand-written `_steps()`

Use `just-makeit method` when you need an execute path with **different
input or output types** than the primary `step()`.

```{02_method_scalar.sh}
```

The command appends a scalar C stub to `native/src/ema/ema_core.c`:

```c
uint32_t ema_quantize(ema_state_t *state, float x);
```

For **1:1-rate batch work** (output count equals input count), write the
`_steps()` companion by hand in the same file:

```{02_method_scalar_batch.c}
```

Then wire it into `native/src/ema/ema_ext.c` following the `ema_steps`
pattern already there.

### Array ownership for hand-written `_steps()`

The Python caller's experience is identical to the auto-generated `steps()`:
pass one input array, get back a new numpy array.

```
call f.quantize_steps(block)
│
├─ ext calls PyArray_SimpleNew(n, uint32)   ← one malloc, every call
│
├─ calls ema_quantize_steps(state, block.data, out.data, n)
│    └─ loop: out[i] = ema_quantize(state, block[i])
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — object holds no reference to it
```

The C function `ema_quantize_steps` takes both pointers, but the ext owns
that allocation — the Python caller never passes or manages an output buffer.

**When to use this pattern**

- You need a different input or output type than the primary `step()`.
- Output count equals input count (1:1 rate).
- Straightforward; no infrastructure beyond the loop.

**When not to use it**

If the maximum output count depends on object state and is knowable at init
time (e.g. a decimator), `--variable-output` is more ergonomic — it removes
the per-call allocation from the caller's responsibility. See §3.
