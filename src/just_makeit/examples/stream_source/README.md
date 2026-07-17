# Stream source example

A **source** is an object that generates samples from internal state with no
input — `steps(n)` hands you `n` fresh samples. Mark it `--streamable` and
just-makeit generates a Pythonic block iterator for free, so instead of the
hand-rolled pull loop:

```python
while True:
    block = osc.steps(256)
    consume(block)
```

you write:

```python
for block in osc.stream(256):
    consume(block)
```

This example builds a free-running ramp oscillator, marks it streamable, and
walks through everything the generator gives you: `stream(block)`, the
`count` cap, the `on_block` hook, and `__iter__`.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_source
# stream_source: PASSED
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

## 1. Scaffold a streamable source

```sh
just-makeit new stream_source_demo
cd stream_source_demo

just-makeit object ramp \
    --arg-type void \
    --return-type float \
    --mutable \
    --streamable \
    --stream-block 256 \
    --state value:float:0.0 \
    --state step_inc:float:1.0
```

The flags that matter:

| Flag                       | Effect                                                                          |
| -------------------------- | ------------------------------------------------------------------------------- |
| `--arg-type void`          | A source — `step()` takes no input, it generates from state.                    |
| `--return-type float`      | Each sample is a `float`; `steps(n)` returns an `NDArray[np.float32]`.           |
| `--mutable`                | `step()` advances state in place (the ramp moves), so the state pointer is non-`const`. |
| `--streamable`             | Generate `stream()` and `__iter__`. For a source, the producer is the built-in `steps`. |
| `--stream-block 256`       | The default block `__iter__` pulls when the caller gives none.                   |
| `--state value:float:0.0`  | The running output value.                                                       |
| `--state step_inc:float:1.0` | How much `value` advances per sample.                                         |

`--streamable` adds a C iterator type (`RampStreamIter`) and a `stream()`
method to the generated extension — nothing else about the object changes.
The manifest records it as a single key:

```toml
[ramp]
arg_type             = "void"
return_type          = "float"
mutable              = "true"
streamable           = "true"
stream_block_default = "256"
```

---

## 2. Implement `step()`

A source's whole algorithm lives in the inline `step()` in
`native/inc/ramp/ramp_core.h`. Replace the generated stub with the ramp
recurrence — emit the current value, then advance it:

```c
/* Implement in native/inc/ramp/ramp_core.h — replace the generated stub.
 *
 * A free-running source: emit the current value, then advance it. `value`
 * and `step_inc` are state fields, so each call resumes where the last one
 * left off — exactly what stream() drives, block by block.
 */
static inline float
ramp_step (ramp_state_t *state)
{
  const float out = state->value;
  state->value += state->step_inc;
  return out;
}
```

That is the only C you write. `steps(n)` (the per-sample loop) and the entire
`stream()` / `__iter__` machinery are generated around it — they call this
`step()` for you. (This very function is spliced into the build and run by the
example's test, so what you read here is exactly what compiles.)

---

## 3. Build and stream from Python

```sh
just-makeit build      # cmake configure + build + wheel
```

Now drive the generated iterator:

```python
"""Drive the generated stream() / __iter__ on the ramp source."""

import numpy as np

from stream_source_demo import Ramp

# stream(block, count=k): a source never drains, so `count` bounds it to k
# blocks of `block` samples. Source blocks are freshly allocated each call, so
# collecting them with list() is safe.
ramp = Ramp(value=0.0, step_inc=1.0)
blocks = list(ramp.stream(4, count=3))
print("3 blocks of 4:", [b.tolist() for b in blocks])
assert [b.shape for b in blocks] == [(4,), (4,), (4,)]
assert np.array_equal(np.concatenate(blocks), np.arange(12, dtype=np.float32))

# on_block(b) fires after each block is consumed (post-yield) — the seam for
# pacing, progress, or tee-to-sink. Here it just records each block's sum.
ramp = Ramp(value=0.0, step_inc=1.0)
sums: list[float] = []
for _ in ramp.stream(
    4, count=2, on_block=lambda b: sums.append(float(b.sum()))
):
    pass  # consume the block; the hook runs right after
print("on_block sums:", sums)
assert sums == [6.0, 22.0]  # [0+1+2+3], [4+5+6+7]

# iter(ramp) streams with stream_block_default (256). A source is infinite, so
# break out yourself — here we just take the first block.
ramp = Ramp(value=0.0, step_inc=1.0)
first = next(iter(ramp))
print("__iter__ default block shape:", first.shape)
assert first.shape == (256,) and first[0] == 0.0 and first[1] == 1.0

print("stream_source demo: OK")
```

What you get from the one `--streamable` flag:

- **`stream(block, *, count=None, on_block=None)`** — yields `NDArray` blocks.
  For a source, `count=None` streams forever; `count=k` stops after `k` blocks.
- **`on_block(block)`** — called *after* each block is yielded and consumed, so
  a pacing hook can account for the consumer's time (e.g.
  `on_block=lambda b: clock.pace(len(b))`). just-makeit owns the loop; the hook
  is the seam you wrap.
- **`__iter__`** — `for blk in ramp:` uses the `--stream-block` default (256).

A source's blocks are independent allocations (`steps()` mallocs a fresh array
each call), so `list(ramp.stream(...))` is safe. A *blockwise* producer
(`--variable-output`) instead returns a zero-copy view into a reused buffer —
see the `stream_blockwise` example for that case and its copy-before-next rule.
