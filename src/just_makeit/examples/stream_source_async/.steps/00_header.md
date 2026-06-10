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
