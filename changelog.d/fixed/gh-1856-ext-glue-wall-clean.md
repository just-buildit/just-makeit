- **Generated CPython glue builds clean under `-Wall -Wextra`** (gh-1856).
    `tp_new` no longer leaves `args` / `kwds` unread, every `PyMethodDef` /
    `PyGetSetDef` table ends in its full-width sentinel, and a
    `METH_KEYWORDS` row casts through `(void (*)(void))` instead of straight
    to `PyCFunction`. The warning sweep no longer waves these through with
    `-Wno-*` flags and now fails on any warning from an `_ext*.c`. Scaffolded
    `_core` stubs still trip `-Wunused-parameter` until the author fills
    them in (gh-1857).
