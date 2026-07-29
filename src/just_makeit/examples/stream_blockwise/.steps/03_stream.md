## 3. Build and stream from Python

```sh
just-makeit build      # cmake configure + build + wheel
```

Now drive the generated iterator:

```{03_demo.py}
```

What the one `--streamable` flag bought you:

- **`stream(block, *, count=None, on_block=None)`** — yields `NDArray` blocks
  from `run(block)` until it returns an empty block (drained), or until `count`
  blocks have been yielded.
- **`on_block(block)`** — runs *after* each block is yielded and consumed; the
  seam for pacing, back-pressure, or progress.
- **`__iter__`** — `for blk in drainer:` uses the default block size.

### Blocks are independent

A `variable_output` producer allocates a NumPy-owned array per call, so every
block a stream yields owns its own memory. Collecting them needs no copy:

```python
chunks = list(drainer.stream(8))   # safe: each block is independent
whole = np.concatenate(chunks)
```

!!! note "This used to require a copy"

    Earlier versions returned a view into a buffer the object reused, so
    blocks aliased each other and `list(...)` silently gave you the last block
    N times. That reuse was removed in gh-604 — see [Array memory
    ownership](../memory-ownership.md).

This is exactly why `on_block` fires *after* the yield: by then the consumer
has already used (or copied) the block, so the buffer is free to be refilled on
the next pull. A *source* producer (`steps`) has no such rule — see the
`stream_source` example.

The hand-written Doxygen `@brief` on `drainer_create()` in the sacred
`native/inc/drainer/drainer_core.h` header drives the generated `drainer.pyi`
class docstring — `jm apply` re-derives the stub from that comment.
