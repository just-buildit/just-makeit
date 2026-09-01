## 2. Add the converter types

```{02_objects.sh}
```

Two types, one `.so`.

### Cf32ToQ15 — writer

Takes a `float _Complex` sample, scales it, clamps it, and packs the
I and Q parts as two `int16_t` values.  Returns the number of bytes written
(`int32_t`, 4 on success, −1 on error) so the caller can detect short writes.

`--arg-type float _Complex` and `--return-type int32_t` generate:

```c
static inline int32_t
cf32_to_q15_step(const cf32_to_q15_state_t *state, float _Complex x);

void cf32_to_q15_steps(cf32_to_q15_state_t *state,
                       const float _Complex *input,
                       int32_t             *output,
                       size_t               n);
```

### Q15ToCf32 — reader

`--arg-type void` makes this a **generator**: no input parameter.  Each
`step()` call reads one complex q15 sample (two `int16_t`) from the file
descriptor stored in `fd` and returns it as a normalised `float _Complex`.

```c
static inline float _Complex
q15_to_cf32_step(const q15_to_cf32_state_t *state);

void q15_to_cf32_steps(q15_to_cf32_state_t *state,
                       float _Complex       *output,
                       size_t               n);
```

`fd` is passed at construction — the caller opens the file with `os.open()`:

```python
import os
from iqfile.conv import Q15ToCf32

fd = os.open("samples.q15", os.O_RDONLY)
reader = Q15ToCf32(fd=fd)
block  = reader.steps(1024)   # returns complex64 ndarray
os.close(fd)
```
