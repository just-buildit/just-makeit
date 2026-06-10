# Stream blockwise example

A **blockwise** producer pulls a block of samples per call and returns however
many it had — a short or empty block once the source runs dry. The classic
shape is a `--variable-output` method: `run(n) -> array`. Mark the object
`--streamable` and just-makeit drives that method with a Pythonic iterator that
**stops on its own when the source drains**, so instead of:

```python
while len(block := decoder.run(4096)):
    consume(block)
```

you write:

```python
for block in decoder.stream(4096):
    consume(block)
```

This example builds a finite "drainer" — a source of exactly `total` complex
samples that empties as you pull it — marks it streamable, and shows the drain,
`count`, `on_block`, and `__iter__`, plus the one gotcha that comes with
zero-copy output: **copy each block before the next call.**

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_blockwise
# stream_blockwise: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold a streamable blockwise producer

```sh
just-makeit new stream_blockwise_demo
cd stream_blockwise_demo

# The object: finite state (total samples, current position), no step().
just-makeit object drainer \
    --arg-type void \
    --return-type "float _Complex" \
    --mutable \
    --streamable \
    --variable-output \
    --state total:int32_t:20 \
    --state pos:int32_t:0
```

The flags that matter:

| Flag                  | Effect                                                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `--variable-output`   | Add a `run(n) -> array` method whose output length is decided at call time (the blockwise producer). |
| `--streamable`        | Generate `stream()` / `__iter__`. With a `variable_output` method present, it drives that method.    |
| `--mutable`           | `run()` advances `pos` in place as the source drains.                                                |
| `--state total:int32_t:20` | Total samples this source will ever emit.                                                       |
| `--state pos:int32_t:0`    | How many have been emitted so far.                                                              |

`--variable-output` makes the object a generator: a pre-allocated output buffer
is sized once at `__init__`, and each `run(n)` returns a **zero-copy view** into
it. `--streamable` notices the `variable_output` method and picks it as the
stream producer (it wins over the built-in `steps`), so `stream()` calls `run`
block by block and stops the moment it returns an empty block.

---

## 2. Implement the producer

A `--variable-output` method generates two stubs in
`native/src/drainer/drainer_core.c`: `drainer_run_max_out()` (the upper bound
on output size) and `drainer_run()` (the producer itself). Fill them in.

The bound — one call can at most return the whole remaining source:

```c
/* Implement in native/src/drainer/drainer_core.c — replace the generated stub.
 *
 * Worst-case output for one call: the whole remaining source. The binding
 * uses this to size the reusable output buffer once, at __init__.
 */
size_t
drainer_run_max_out (drainer_state_t *state)
{
  return (size_t)state->total;
}
```

The producer — emit up to `n` samples, advance `pos`, and return the count:

```c
/* Implement in native/src/drainer/drainer_core.c — replace the generated stub.
 *
 * Emit up to n of the remaining samples as an ascending complex ramp, advance
 * pos, and return the count. The empty return once drained (pos == total) is
 * what makes stream() terminate.
 */
size_t
drainer_run (drainer_state_t *state, size_t n, float complex *out)
{
  int32_t avail = state->total - state->pos;
  if (avail < 0)
    avail = 0;
  size_t k = (size_t)avail < n ? (size_t)avail : n;
  for (size_t i = 0; i < k; i++)
    out[i] = (float complex) (float)(state->pos + (int32_t)i);
  state->pos += (int32_t)k;
  return k;
}
```

That is all the C. The output buffer, the zero-copy numpy view, and the
`stream()` / `__iter__` iterator are generated around these two functions.
(Both are spliced into the build and run by the example's test, so what you
read here is exactly what compiles.)

---

## 3. Build and stream from Python

```sh
just-makeit build      # cmake configure + build + tests
```

Now drive the generated iterator:

```python
"""Drive the generated stream() / __iter__ on the finite drainer."""

import numpy as np

from stream_blockwise_demo import Drainer

# stream(8) over total=20 yields blocks of 8, 8, 4, then stops on the empty
# (drained) block. A variable_output producer returns a zero-copy VIEW into a
# reused buffer, so copy each block before pulling the next one.
d = Drainer(total=20, pos=0)
collected = [block.copy() for block in d.stream(8)]
print("drained in blocks:", [b.shape[0] for b in collected])
assert [b.shape for b in collected] == [(8,), (8,), (4,)]
assert collected[0].dtype == np.complex64
assert np.array_equal(
    np.concatenate(collected).real, np.arange(20, dtype=np.float32)
)

# count caps the iteration no matter how much source is left.
d = Drainer(total=20, pos=0)
first = next(d.stream(8, count=1))
print("count=1 → one block of", first.shape[0])
assert first.shape == (8,)

# on_block(b) fires after each consumed block (post-yield).
d = Drainer(total=20, pos=0)
sizes: list[int] = []
for _ in d.stream(8, on_block=lambda b: sizes.append(len(b))):
    pass
print("on_block sizes:", sizes)
assert sizes == [8, 8, 4]

# iter(drainer) uses the default block (1024 >= the 20 remaining), so the whole
# source comes back as one block, then drains.
d = Drainer(total=20, pos=0)
blocks = [b.copy() for b in d]
print("__iter__ blocks:", [b.shape[0] for b in blocks])
assert len(blocks) == 1 and blocks[0].shape == (20,)

print("stream_blockwise demo: OK")
```

What the one `--streamable` flag bought you:

- **`stream(block, *, count=None, on_block=None)`** — yields `NDArray` blocks
  from `run(block)` until it returns an empty block (drained), or until `count`
  blocks have been yielded.
- **`on_block(block)`** — runs *after* each block is yielded and consumed; the
  seam for pacing, back-pressure, or progress.
- **`__iter__`** — `for blk in drainer:` uses the default block size.

### The zero-copy rule

A `variable_output` producer returns a **view into a reused buffer**, so two
blocks pulled from the same object alias the same memory. Consume — or
`.copy()` — each block before the next iteration:

```python
chunks = [b.copy() for b in drainer.stream(8)]   # safe: each copied
chunks = list(drainer.stream(8))                  # WRONG: all alias the last
```

This is exactly why `on_block` fires *after* the yield: by then the consumer
has already used (or copied) the block, so the buffer is free to be refilled on
the next pull. A *source* producer (`steps`) has no such rule — see the
`stream_source` example.
