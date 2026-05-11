## 4. `method --variable-output --multi-output` — parallel output streams

`--multi-output TYPE` adds a second pre-allocated output buffer alongside the
primary one.  The Python call returns a tuple.  The flag is repeatable for
three or more streams.

```{04_multi_output.sh}
```

Generated stubs in `hbdecim_methods.c`:

```c
size_t hbdecim_execute_ovf_max_out(hbdecim_state_t *state);
size_t hbdecim_execute_ovf(hbdecim_state_t    *state,
                           const float complex *in, size_t n_in,
                           float complex       *out,
                           uint8_t             *ovf);
```

Both `out` and `ovf` are pre-allocated to `_max_out()` elements and owned by
the object.  Your implementation fills both and returns the count:

```{04_execute_ovf.c}
```

### What Python sees

```python
import numpy as np
from my_decim import Hbdecim

d = Hbdecim()

block    = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
samples, flags = d.execute_ovf(block)   # tuple of two zero-copy views
```

### Array ownership for multi-output

```
d = Hbdecim()
│
└─ ext mallocs float complex[512]  → d._out_buf
   ext mallocs uint8_t[512]        → d._ovf_buf
   both stored in the object

samples, flags = d.execute_ovf(block)
│
├─ calls hbdecim_execute_ovf(..., d._out_buf, d._ovf_buf) → returns 512
│
├─ returns (view into d._out_buf[:512],
│           view into d._ovf_buf[:512])
│
│  ownership: object retains both buffers
│  lifetime:  both views stale after next call to execute_ovf()
│             — copy before calling again

n_ovf = int(flags.sum())           # safe — flags is still valid here
samples_copy = samples.copy()      # independent; survives next call
```

The same "stale after next call" rule applies to every buffer produced by
`--variable-output`.  The zero-copy design makes the steady-state path
allocation-free; the copy obligation is the trade-off.
