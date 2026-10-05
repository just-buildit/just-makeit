## 4. `method --variable-output --multi-output` — parallel output streams

`--multi-output TYPE` adds a second output array alongside the primary one;
each call allocates both from NumPy and returns them independently owned.  The Python call returns a tuple.  The flag is repeatable for
three or more streams.

```{04_multi_output.sh}
```

The command declares two more C functions in `hbdecim_core.h` and appends
their stubs to `hbdecim_core.c`:

```c
size_t my_decim_hbdecim_execute_ovf_max_out(my_decim_hbdecim_state_t *state,
                                            size_t n_in);
size_t my_decim_hbdecim_execute_ovf(my_decim_hbdecim_state_t *state,
                                    const float _Complex *in, size_t n_in,
                                    float _Complex *out, uint8_t *out1);
```

Both `out` and the secondary array `out1` are allocated by the ext on every
call, NumPy-owned, `max(execute_ovf_max_out(n_in), n_in)` elements each. Your
implementation fills both and returns the count:

```{04_execute_ovf.c}
```

### What Python sees

```python
import numpy as np
from my_decim import Hbdecim

d = Hbdecim()

block    = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
samples, flags = d.execute_ovf(block)   # tuple of two new, independently owned arrays
```

### Array ownership for multi-output

```
samples, flags = d.execute_ovf(block)
│
├─ ext allocates complex64[max(execute_ovf_max_out(1024), 1024)]
│  and uint8[same], both NumPy-owned
│
├─ calls my_decim_hbdecim_execute_ovf(state, block.data, 1024, out, out1)  → returns 512
│
└─ returns (out, out1), each trimmed to 512
   ownership: the caller owns both arrays
   lifetime:  independent of the object and of every other result
```

As in §3, every result is independent; nothing needs copying before the next
call. Unlike `execute()`, a multi-output method takes no `out=` buffer.
