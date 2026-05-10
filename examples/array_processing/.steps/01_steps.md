## 1. Auto-generated `steps()` — free with every object

```{01_scaffold.sh}
```

Every `just-makeit object` generates both `step()` and `steps()`:

| C function | Signature |
|---|---|
| `ema_step` | `float ema_step(ema_state_t *s, float x)` |
| `ema_steps` | `void ema_steps(ema_state_t *s, const float *in, float *out, size_t n)` |

`steps()` is a thin loop in `native/src/ema/ema_core.c` — it calls `step()`
once per sample. You implement `step()`; `steps()` comes for free.

### What Python sees

```python
import numpy as np
from my_arrays import Ema

f = Ema(alpha=0.1)

block = np.random.randn(1024).astype(np.float32)
out   = f.steps(block)   # returns np.ndarray, shape (1024,), dtype float32
```

`steps()` allocates a fresh numpy array on every call (`PyArray_SimpleNew`) and
returns it. The caller owns that array outright — the object holds no reference
to it and never touches it again.

### The C API — caller-supplied pointers, no allocation

At the C level, `steps()` takes both pointers from the caller and allocates
nothing:

```c
/* Output buffer must be pre-allocated by caller. */
void ema_steps(ema_state_t       *state,
               const float       *input,
               float             *output,
               size_t             n);
```

This is true with or without `--perf`: `JM_DEFINE_STEPS` only replaces the
loop body (adding SIMD dispatch), not the signature or the allocation model.

### The Python ext — one malloc per call

The ext is the only place an allocation happens. It calls `PyArray_SimpleNew`
to create the output array, passes the raw pointer to `ema_steps`, then
returns the numpy array to the caller:

```
call f.steps(block)
│
├─ ext calls PyArray_SimpleNew(n)   ← one malloc, every call
│
├─ calls ema_steps(state, block.data, out.data, 1024)
│    └─ no allocation inside; fills out[] in place
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — safe to hold, copy, or discard at will
```

Successive calls are independent: the previous result is never overwritten.
This is the opposite of `--variable-output` (§3), where the object owns a
fixed buffer and reuses it each call.

### Eliminating the per-call malloc with `out=`

Pass a pre-allocated numpy array as the second argument and the ext writes
directly into it — `PyArray_SimpleNew` is skipped entirely:

```python
buf = np.empty(1024, dtype=np.float32)   # allocate once

for block in stream:
    f.steps(block, buf)   # zero allocation on the hot path
```

The returned object is the same array you passed in (`ret is buf`), so you
can ignore the return value or use it for chaining. The buffer must be
C-contiguous, the correct dtype, and at least as long as the input.

```
call f.steps(block, buf)
│
├─ ext validates buf: dtype, C-contiguous, len == n
│
├─ calls ema_steps(state, block.data, buf.data, 1024)
│    └─ no allocation; fills buf in place
│
└─ returns buf (same object, new reference)
   ownership: caller retains
   lifetime:  safe to reuse immediately on next call
```

This is the right choice for any processing loop where throughput matters.
For one-shot calls or exploratory work the default (no `out=`) is simpler.

### Inline array state — no heap per field

If your object has fixed-length array state (e.g. `--state "coeffs:float[16]"`),
those arrays live **inside the C struct**, not on the heap:

```c
typedef struct {
    float  coeffs[16];   /* inline — no extra malloc */
    float  delay[16];    /* inline */
    float  gain;
} ema_state_t;
```

`ema_create()` does exactly one `malloc` for the whole struct. There is no
`malloc` per field, no pointer to chase, and no fragmentation.

Contrast this with a hypothetical `float *coeffs` pointer: that would require
a separate allocation, a separate free, and careful ownership accounting.
just-makeit avoids this entirely by embedding arrays inline whenever the length
is fixed at code-generation time.
