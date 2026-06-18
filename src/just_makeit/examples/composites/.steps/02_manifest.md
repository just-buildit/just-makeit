## The handle manifest

One `[module.ring]` section, `kind = "handle"`, declares the whole class. jm
reads it and generates everything — `ring_ext.c`, `CMakeLists.txt`, `ring.pyi`:

```{manifest.toml}
```

The pieces:

- **`create_fn` / `close_fn`** — the open/close lifecycle. `close()` is
  idempotent and `tp_dealloc` closes a forgotten handle, so the resource always
  releases. `context_manager = true` also emits `__enter__` / `__exit__`.
- **`methods`** — `push` is an **array-in** method (numpy-marshaled), returning
  the count accepted; `pop` is an **int-in -> array-out** method returning an
  independent numpy-owned array (never a dangling view).
- **`getters`** — the decoded-getter property, the genuinely handle-specific
  code. `ringbuf_stats()` fills a live struct; `used` reads a field directly,
  `fill_fraction` is a derived `expr`. A scalar return-by-value getter whose
  field names a `writable_fn` becomes the read/write `gain` property.

Two naming details keep the build clean:

- the module is named **`ring`**, distinct from the **`ringbuf`** backing and
  c_dep, so the c_dep subdirectory and the handle target never collide on a
  shared `native/src/ringbuf/` binary dir;
- **`package = "."`** lands `ring.so` in the package root, so the import is the
  clean `from composites.ring import Ring` instead of `composites.ring.ring`.

`depends_on = [{ name = "ringbuf", link = true }]` is what makes the resource
link onto the module: jm adds the `ringbuf_core` OBJECT lib directly to
`ring`'s `target_link_libraries` (CMake does not pull OBJECT-lib objects into a
final `.so` transitively, so the link must be direct).
