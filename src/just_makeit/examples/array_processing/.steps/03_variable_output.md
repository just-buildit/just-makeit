## 3. `method --variable-output` — self-sizing batch

Use this when the **maximum output count is bounded by state and knowable at
init time**.  The classic case is a rate-changing block: a 2× decimator with
block size `B` can produce at most `ceil(B / 2)` outputs per call.

```{03_variable_output.sh}
```

The command appends two C stubs to `native/src/hbdecim/hbdecim_core.c`:

| Stub                                    | When called               | Your job                        |
| --------------------------------------- | ------------------------- | ------------------------------- |
| `hbdecim_execute_max_out(state)`        | Once at Python `__init__` | Return the output bound         |
| `hbdecim_execute(state, in, n_in, out)` | Every Python call         | Fill `out`, return actual count |

Implement both:

```{03_max_out.c}
```

### What Python sees

```python
import numpy as np
from my_decim import Hbdecim

d = Hbdecim()

block = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
out = d.execute(block)   # a new array, shape (≤512,)
```

`d.execute(block)` returns a **NumPy-owned array**, sized
`max(execute_max_out(), n)` and trimmed to the count the kernel reported.

### Array ownership for `--variable-output`

```
out = d.execute(block)
│
├─ ext allocates a NumPy array of max(execute_max_out(), 1024)
│  └─ the kernel writes straight into it — no copy
│
├─ calls hbdecim_execute(state, block.data, 1024, out.data)  → returns 512
│
└─ returns it trimmed to 512
   ownership: the returned array owns its memory
   lifetime:  independent of the object and of every other result
```

Every result is independent. Accumulating them is safe, and always was
intended to be:

```python
chunks = [d.execute(b) for b in blocks]   # each keeps its own data
whole = np.concatenate(chunks)
```

Nothing the object does later can disturb an array you already hold — not a
same-size call, not a larger one, not `destroy()`.

!!! note "This used to be a constraint, and no longer is"

    Earlier versions returned a view into a buffer the object reused, so a
    result went stale on the next call and had to be copied. Two mechanisms
    were built to make that safe (gh-219, gh-437) before the approach was
    abandoned in gh-604 — measurement showed it retained ~514 KiB per call and
    ran 6-8× slower than simply allocating. If you have code that defensively
    copies each result, you can drop the copy.

To write into your own buffer instead, pass `out=` — see
[Array memory ownership](../memory-ownership.md) for when that is worth it.

### When to use `--variable-output`

| Use case                         | `_max_out` returns | Appropriate?                                      |
| -------------------------------- | ------------------ | ------------------------------------------------- |
| Decimator, ratio R, block size B | `ceil(B / R)`      | Yes                                               |
| FIFO with fixed capacity C       | `C`                | Yes                                               |
| FIR filter, 1:1 rate             | unknown at init    | No — output size = input size; use auto `steps()` |
| Integrator / accumulator         | 1 per sample       | No — use scalar `step()`                          |
| Overflow detector, 1:1 rate      | unknown at init    | No — use scalar method + hand-written `_steps()`  |
