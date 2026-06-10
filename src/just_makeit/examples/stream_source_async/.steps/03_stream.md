## 3. Build and stream from `asyncio`

```sh
just-makeit build      # cmake configure + build + tests
```

Now drive the generated iterator under an event loop:

```{03_demo.py}
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
