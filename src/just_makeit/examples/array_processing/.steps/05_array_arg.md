## 5. `--arg-type type[]` — array-buffer primary arg

Some objects are designed to consume an entire buffer in one call — a
decimator, a packet framer, a block codec.  Wrapping them with a scalar
`step()` + auto-generated `steps()` adds indirection that compilers cannot
always eliminate.  Pass `[]` on the arg type to express this directly.

```{05_array_arg.sh}
```

The generated `step()` takes a pointer and a length; Python passes it a numpy
array:

```c
/* native/inc/my_buf/buf_proc/buf_proc_core.h */
static inline int
my_buf_buf_proc_step(
    my_buf_buf_proc_state_t *state,
    const float _Complex *x, size_t x_len)
{
    (void)state; (void)x; (void)x_len; /* TODO: implement */
    return (int)0;
}
```

`steps()` is **not** generated — the primary operation already takes a buffer.

### What Python sees

```python
import numpy as np
from my_buf import BufProc

proc = BufProc()
block = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
n = proc.step(block)   # passes the whole array; returns int
```

### Type stub (`my_buf/src/my_buf/buf_proc.pyi`)

```python
class BufProc:
    def __init__(self, count: int = 0) -> None: ...
    def step(self, x: npt.NDArray[np.complex64]) -> int:
        """Process one input sample."""
    # no steps() — the primary op already takes a buffer
```

### Choosing between the five patterns

```
Does output count equal input count?
├─ Yes, and input is one sample → use step() + auto steps()          (§1)
│
├─ Yes, but a method has a different return type → jm method --batch (§2)
│
├─ No → can the output count be bounded from the input length?
│       ├─ Yes, one stream  → --variable-output                       (§3)
│       └─ Yes, N streams   → --variable-output --multi-output        (§4)
│
└─ Primary op takes a whole buffer → --arg-type type[]                (§5)
   (no steps() generated; step() accepts NDArray directly)
```
