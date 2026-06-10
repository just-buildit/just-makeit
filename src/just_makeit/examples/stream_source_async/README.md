# Async stream source example

This is the [`stream_source`](../stream_source/README.md) example turned
asynchronous. A **source** generates samples from internal state; `--streamable`
already gives it a Pythonic `for blk in obj.stream(...)`. Adding
`--async-stream` *also* makes it work under `asyncio`:

```python
async for block in osc.stream(256):
    await sink.write(block)
```

`__anext__` runs each producer step in the running event loop's **default
executor**, so a `nogil` producer lets the loop keep serving other tasks while
the kernel computes — and on a drained source it raises `StopAsyncIteration`.
It is opt-in: a plain `--streamable` object stays sync-only.

This example builds the same free-running ramp oscillator, marks it
`--async-stream`, and drives it with `async for` (over `stream(...)` and over
the object itself), with the sync forms still available on the same type.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example stream_source_async
# stream_source_async: PASSED
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

## 1. Scaffold an async-streamable source

```sh
just-makeit new stream_source_async_demo
cd stream_source_async_demo

just-makeit object ramp \
    --arg-type void \
    --return-type float \
    --mutable \
    --async-stream \
    --stream-block 256 \
    --state value:float:0.0 \
    --state step_inc:float:1.0
```

The only change from the sync [`stream_source`](../stream_source/README.md)
example is `--async-stream` in place of `--streamable`:

| Flag                       | Effect                                                                          |
| -------------------------- | ------------------------------------------------------------------------------- |
| `--arg-type void`          | A source — `step()` takes no input, it generates from state.                    |
| `--return-type float`      | Each sample is a `float`; `steps(n)` returns an `NDArray[np.float32]`.           |
| `--mutable`                | `step()` advances state in place (the ramp moves), so the state pointer is non-`const`. |
| `--async-stream`           | Generate `stream()` / `__iter__` **and** `__aiter__` / `__anext__`. Implies `--streamable`. |
| `--stream-block 256`       | The default block `__iter__` / `__aiter__` pulls when the caller gives none.     |
| `--state value:float:0.0`  | The running output value.                                                       |
| `--state step_inc:float:1.0` | How much `value` advances per sample.                                         |

`--async-stream` adds, on top of the synchronous iterator, a `PyAsyncMethods`
slot (`__aiter__` / `__anext__`) on the `RampStreamIter` type and an
`__aiter__` on the object — all in C. The manifest records one extra key:

```toml
[ramp]
arg_type             = "void"
return_type          = "float"
mutable              = "true"
streamable           = "true"
async_stream         = "true"
stream_block_default = "256"
```

---

## 2. Implement `step()`

The algorithm is unchanged from the sync example — async iteration reuses the
exact same producer. Replace the inline `step()` stub in
`native/inc/ramp/ramp_core.h` with the ramp recurrence:

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

That is the only C you write. `steps(n)`, the sync `stream()` / `__iter__`, and
the async `__aiter__` / `__anext__` are all generated around this one `step()`
— `__anext__` just calls it from the event loop's executor.

---

## 3. Build and stream from `asyncio`

```sh
just-makeit build      # cmake configure + build + tests
```

Now drive the generated iterator under an event loop:

```python
"""Drive the generated async stream() / __aiter__ on the ramp source."""

import asyncio

import numpy as np

from stream_source_async_demo import Ramp


async def main() -> None:
    # async for over stream(block, count=k): same semantics as the sync form,
    # but each producer step runs in the event loop's default executor, so a
    # nogil producer would let other tasks run while the kernel works.
    ramp = Ramp(value=0.0, step_inc=1.0)
    blocks = []
    async for b in ramp.stream(4, count=3):
        blocks.append(b.copy())
    print("3 blocks of 4:", [b.tolist() for b in blocks])
    assert [b.shape for b in blocks] == [(4,), (4,), (4,)]
    assert np.array_equal(
        np.concatenate(blocks), np.arange(12, dtype=np.float32)
    )

    # on_block(b) fires after each block is consumed (post-yield) — the seam
    # for pacing, progress, or tee-to-sink. It stays a plain (sync) callable.
    ramp = Ramp(value=0.0, step_inc=1.0)
    sums: list[float] = []
    async for _ in ramp.stream(
        4, count=2, on_block=lambda b: sums.append(float(b.sum()))
    ):
        pass  # consume the block; the hook runs right after
    print("on_block sums:", sums)
    assert sums == [6.0, 22.0]  # [0+1+2+3], [4+5+6+7]

    # `async for blk in obj` uses stream_block_default (256). A source is
    # infinite, so break out yourself — here we just take the first block.
    ramp = Ramp(value=0.0, step_inc=1.0)
    async for first in ramp:
        print("async for obj, default block:", first.shape)
        assert first.shape == (256,)
        assert first[0] == 0.0 and first[1] == 1.0
        break

    # The sync iterator is still there on the very same object.
    ramp = Ramp(value=0.0, step_inc=1.0)
    assert [b.shape for b in ramp.stream(5, count=2)] == [(5,), (5,)]
    print("sync stream still works")


asyncio.run(main())
print("stream_source_async demo: OK")
```

What `--async-stream` adds on top of the sync iterator:

- **`async for blk in obj.stream(block, *, count=None, on_block=None)`** — the
  same semantics as the sync `stream()` (count cap, post-yield `on_block`,
  drain-stop), but awaitable.
- **`async for blk in obj`** — uses the `--stream-block` default (256 here).
- Each `__anext__` runs the producer step via
  `loop.run_in_executor(None, ...)`. That genuinely frees the loop during the
  kernel **only if the producer releases the GIL** — i.e. a `nogil` method (jm
  supports `jm method --nogil`). For a plain producer, `async for` still works
  and yields control between blocks; it just doesn't overlap the kernel itself.

The sync `for blk in obj.stream(...)` / `for blk in obj` forms are untouched and
work on the same object — `--async-stream` only *adds* the async surface. See
the [`stream_source`](../stream_source/README.md) example for the synchronous
walkthrough, and [`stream_blockwise`](../stream_blockwise/README.md) for a
finite (draining) producer.
