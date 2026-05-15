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

______________________________________________________________________

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

| Stub                                 | Called by ext      | Your job                           |
| ------------------------------------ | ------------------ | ---------------------------------- |
| `chunker_push_max_out(state)`        | Once at `__init__` | Return max output samples possible |
| `chunker_push(state, in, n_in, out)` | Every Python call  | Fill `out[]`, return actual count  |

______________________________________________________________________

## 2. Implement

Replace both stubs in `native/src/chunker/chunker_core.c`.

Generated stubs:

```c
/* <<IMPLEMENT>> return max samples chunker_push can ever produce */
size_t
chunker_push_max_out(chunker_state_t *state)
{
    (void)state;
    return 0; /* TODO */
}

/* <<IMPLEMENT>> process input and write results to out[]; return count written */
size_t
chunker_push(chunker_state_t *state, const float complex *in, size_t n_in,
             float complex *out)
{
    (void)state; (void)in; (void)out;
    return 0; /* TODO */
}
```

Implementation:

```c
size_t
chunker_push_max_out(chunker_state_t *state)
{
    /* buf[] holds 256 samples — the absolute output ceiling.
     * Push at most (256 - n_buf) samples per call to stay safe. */
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

The patch script automates this replacement:

```sh
python3 .steps/02_patch.py
```

______________________________________________________________________

## 3. Build and test

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

______________________________________________________________________

## 4. Use from Python

```python
"""Integration test: feed irregular bursts into Chunker, verify chunk output.

Run from the project root after building:
    python3 .steps/04_demo.py

Constraint: with chunk_size=64 and an internal buf[256], the output buffer
pre-allocated by --variable-output holds 256 samples (4 complete chunks).
Callers must not push more samples in one call than the output buffer can hold:
  max safe push = floor(256 / chunk_size) * chunk_size - current_n_buf
For chunk_size=64 and worst-case n_buf=63: max push ≈ 192 samples.
"""

import sys
import pathlib

# Add the built extension to sys.path
build_dir = pathlib.Path("build")
for p in build_dir.rglob("my_chunker*.so"):
    sys.path.insert(0, str(p.parent))
    break
sys.path.insert(0, str(pathlib.Path("src")))

import numpy as np
from my_chunker import Chunker

CHUNK = 64

c = Chunker(chunk_size=CHUNK)

# Irregular bursts that collectively push 281 samples.
# Max single burst = 180; worst-case output = 3 chunks = 192 samples < 256.
bursts = [7, 50, 1, 40, 180, 3]      # sum = 281
# Expected: floor(281 / 64) = 4 complete chunks (256 samples), 25 buffered.

collected = []   # copies of complete-chunk views
total_in = 0

for size in bursts:
    block = np.ones(size, dtype=np.complex64) * complex(total_in, 0)
    view = c.push(block)
    # view is a zero-copy slice of the object's internal output buffer.
    # It becomes stale on the next push() call — copy immediately.
    if len(view):
        assert len(view) % CHUNK == 0, \
            f"output length {len(view)} is not a multiple of chunk_size {CHUNK}"
        collected.append(view.copy())
    total_in += size

total_out = sum(len(v) for v in collected)
assert total_out == 4 * CHUNK, \
    f"expected {4 * CHUNK} output samples, got {total_out}"

# Verify that the first burst (7 samples) produced no output
assert len(collected[0]) >= CHUNK, "first non-empty view should be ≥ one chunk"

# reset() clears the accumulator; next push starts fresh
c.reset()
view = c.push(np.zeros(CHUNK, dtype=np.complex64))
assert len(view) == CHUNK, "after reset: one full chunk in → one chunk out"

print("stream_chunker demo: PASSED")
print(f"  fed {total_in} samples in {len(bursts)} irregular bursts")
print(f"  received {total_out} output samples ({total_out // CHUNK} complete "
      f"{CHUNK}-sample chunks)")
print(f"  {total_in - total_out} samples remain buffered (flushed on reset)")
print(f"  {len(collected)} non-empty push() calls (some bursts produced 0 output)")
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

______________________________________________________________________

## 5. reset()

`reset()` sets `n_buf = 0` and zeroes `buf` — any partially accumulated
samples are discarded.  Useful at stream boundaries or after error recovery.

```python
c.reset()
# Next push() starts with an empty accumulation buffer
```
