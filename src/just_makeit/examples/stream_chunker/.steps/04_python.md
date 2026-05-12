## 4. Use from Python

```python
import numpy as np
from my_chunker import Chunker

CHUNK = 64
c = Chunker(chunk_size=CHUNK)

# Feed samples in irregular bursts — output varies per call.
for block in audio_source():
    view = c.push(block)

    # view is a zero-copy slice of the object's internal output buffer.
    # len(view) is always a multiple of chunk_size (including zero).
    if len(view) == 0:
        continue    # not enough data yet — keep feeding

    # Copy before the next push() call, which overwrites the same buffer.
    chunks = view.copy().reshape(-1, CHUNK)
    for chunk in chunks:
        process(chunk)
```

### Memory ownership diagram

```
c = Chunker(chunk_size=64)
│
└─ ext calls chunker_push_max_out()  → 256
   ext mallocs float complex[256]    ← one malloc, at __init__
   stored as c._out_buf (opaque)

view = c.push(block)
│
├─ ext calls chunker_push(state, block.data, len(block), c._out_buf)
│    → returns n_out (multiple of 64; may be 0)
│
└─ returns numpy view wrapping c._out_buf[:n_out]
   ownership: object retains the buffer
   lifetime:  view is stale after the NEXT push() — copy immediately
```

### Output size constraint

`push_max_out` returns 256 (the internal buffer capacity).  The ext
pre-allocates exactly 256 output samples.  A single `push()` call must
not produce more than 256 output samples.

With `chunk_size=64`: each call can emit at most `floor(256 / 64) = 4`
complete chunks.  The safe maximum input per call is:

```
max_safe_push = 4 * chunk_size - current_n_buf
              = 256 - current_n_buf
              ≤ 256 samples
```

For larger inputs, split into ≤192-sample slices before calling `push()`.
