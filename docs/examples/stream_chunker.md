# stream_chunker example

A stream re-framer: accepts samples in arbitrary-size bursts and emits them
as fixed-size chunks.  Demonstrates **variable-size input with variable-size
output** using `--variable-output` and `--no-step`.

The key concept: some calls produce zero chunks (not enough data yet); others
produce one or several.  The Python caller never knows in advance how many
samples will come back — it just checks `len(view)` after each call.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_chunker
# stream_chunker: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

---

## 1. Scaffold

```sh
just-makeit new my_chunker
cd my_chunker

just-makeit object chunker \
    --state "chunk_size:int32_t:64" \
    --state "buf:float _Complex[256]" \
    --state "n_buf:int32_t:0" \
    --no-step

just-makeit method chunker push \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --variable-output
```

`--no-step` suppresses `step()` and `steps()` — the only interface is `push()`.

`--variable-output` generates two C stubs in `chunker_core.c`:

| Stub | Called by ext | Your job |
|------|---------------|----------|
| `chunker_push_max_out(state)` | Once at `__init__` | Return max output samples possible |
| `chunker_push(state, in, n_in, out)` | Every Python call | Fill `out[]`, return actual count |

---

## 2. Implement

Replace the stubs in `native/src/chunker/chunker_core.c`:

```c
size_t
chunker_push_max_out(chunker_state_t *state)
{
    /* buf[] holds 256 samples.  That is the absolute output ceiling. */
    (void)state;
    return 256;
}

size_t
chunker_push(chunker_state_t *state, const float complex *in, size_t n_in,
             float complex *out)
{
    size_t n_out = 0;
    for (size_t i = 0; i < n_in; i++) {
        state->buf[state->n_buf++] = in[i];
        if (state->n_buf >= state->chunk_size) {
            memcpy(out + n_out, state->buf,
                   (size_t)state->chunk_size * sizeof(float complex));
            n_out += (size_t)state->chunk_size;
            state->n_buf = 0;
        }
    }
    return n_out;
}
```

`memcpy` and `complex.h` are already included via `clib_common.h`.

---

## 3. Build and test

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

---

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

---

## 5. reset()

`reset()` sets `n_buf = 0` and zeroes `buf` — any partially accumulated
samples are discarded.  Useful at stream boundaries or after error recovery.

```python
c.reset()
# Next push() starts with an empty accumulation buffer
```
