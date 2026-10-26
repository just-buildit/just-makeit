- **A teardown from inside argument conversion no longer reaches the
    kernel** (gh-2273). A generated method checked its handle once, at the
    top, then converted its arguments; converting can run Python (an
    `__array__`), and Python that called `destroy()` left the method a NULL
    state, so the kernel (or `<m>_max_out`) was called with NULL and the
    process segfaulted. The guard now runs again after the last conversion,
    through the one kernel-call emitter, releasing what the call holds and
    raising the same `RuntimeError("destroyed")`. Handle and composer
    modules build their own calls and are tracked in gh-2380.
