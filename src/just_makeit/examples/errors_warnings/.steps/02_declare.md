## 2. Declare the four channels

```{02_declare.sh}
```

What each one is really doing:

- **`just-makeit error`** replaces jm's default translation of a `NULL` from
    `create()`. Without it every refusal surfaces as `MemoryError`, which is
    true only for an actual allocation failure and misleading for every other
    reason — and uncatchable the way a caller would naturally reach for it.
    The command prints the limit of the design as it runs: NULL is NULL, so
    *every* failure now reports as `ValueError`, a genuine out-of-memory
    included.
- **`just-makeit warning`** exists because C has no channel for "succeeded,
    but". `create()` returns a pointer or it does not. So the component writes
    a `bool` on its state, and `--condition degraded` tells jm to emit a
    `PyErr_WarnEx` reading that field right after a successful construction.
- **`--status-return`** says the `int` is *only* a status. The method returns
    `None` in Python, and any non-zero raises.
- **`--error-negative`** says the `int` is a **value** unless it is negative.
    `peek()` returns an `int` in Python, and only a negative raises.

Both method flags need `--return-type int`. That is not cosmetic:
`--status-return` with `--return-type size_t` is accepted and generates
`int _rc = allocator_take(...)` against a `size_t` prototype, which compiles
and silently truncates.
