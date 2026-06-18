## The hand-written resource

The only code you write is the resource itself — here a small FIFO ring
buffer. It opens and closes (the create/close lifecycle), pushes and pops
arrays (the methods), fills a `*_stats_t` struct (the decoded getters read it),
and has a gain get/set pair (the writable property).

The public header lives under `native/inc/ringbuf/`:

```{ringbuf.h}
```

The implementation is a pure-C OBJECT library under `native/src/ringbuf/`,
vendored as a `[project] c_deps` entry — no Python wrapper of its own:

```{ringbuf_cmake.cmake}
```
