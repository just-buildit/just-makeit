## 3. `method --variable-output` — pre-allocated, zero-copy batch

Use this when the **maximum output count is bounded by state and knowable at
init time**.  The classic case is a rate-changing block: a 2× decimator with
block size `B` can produce at most `ceil(B / 2)` outputs per call.

```{03_variable_output.sh}
```

The command generates two C stubs in `native/src/hbdecim/hbdecim_methods.c`:

| Stub | When called | Your job |
|------|-------------|----------|
| `hbdecim_execute_max_out(state)` | Once at Python `__init__` | Return the output bound |
| `hbdecim_execute(state, in, n_in, out)` | Every Python call | Fill `out`, return actual count |

Implement both:

```{03_max_out.c}
```

### What Python sees

```python
import numpy as np
from my_decim import Hbdecim

d = Hbdecim()           # __init__ calls execute_max_out(); mallocs output buffer once

block = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
view  = d.execute(block)  # returns zero-copy view; shape (≤512,)
```

`d.execute(block)` returns a **numpy view** into the object's internal output
buffer.  No allocation happens on this call path at all.

### Array ownership for `--variable-output`

```
d = Hbdecim()
│
└─ ext calls hbdecim_execute_max_out()  → 512
   ext mallocs float complex[512]       ← one malloc, at __init__
   stored as d._out_buf (opaque)

view = d.execute(block)
│
├─ calls hbdecim_execute(state, block.data, 1024, d._out_buf)  → returns 512
│
└─ returns numpy view wrapping d._out_buf[:512]
   ownership: object retains the buffer
   lifetime:  view is valid until the NEXT call to d.execute()
              — do not hold the view across calls; copy if you need to keep it

# Safe: process, then copy if needed
view = d.execute(block)
keep = view.copy()       # independent array, survives next call
```

**Critical constraint**: the view becomes **stale on the next `execute()` call**
because the object overwrites the same buffer.  Copy before calling again if
you need to retain more than one block.

### When to use `--variable-output`

| Use case | `_max_out` returns | Appropriate? |
|---|---|---|
| Decimator, ratio R, block size B | `ceil(B / R)` | Yes |
| FIFO with fixed capacity C | `C` | Yes |
| FIR filter, 1:1 rate | unknown at init | No — output size = input size; use auto `steps()` |
| Integrator / accumulator | 1 per sample | No — use scalar `step()` |
| Overflow detector, 1:1 rate | unknown at init | No — use scalar method + hand-written `_steps()` |
