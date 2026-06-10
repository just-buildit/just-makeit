## 3. Build and stream from Python

```sh
just-makeit build      # cmake configure + build + tests
```

Now drive the generated iterator:

```{03_demo.py}
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
