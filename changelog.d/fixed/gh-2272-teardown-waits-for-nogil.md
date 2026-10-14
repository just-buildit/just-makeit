- **A `close()` on another thread no longer frees the state under a
    running `nogil` call** (gh-2272). Since 0.105.0 a `nogil` method really
    releases the GIL, and the `destroy()` / `close()` / `__exit__` the same
    binding generates freed the state without asking whether a released call
    was still running on it: one thread recording into a `Writer` while
    another closed it crashed with SIGSEGV every time. Every released call
    is now counted on the object, and every explicit teardown first refuses
    new calls (`RuntimeError`), then waits with the GIL released for the
    running ones to return, then frees, so the call returns its result and
    `close()` returns once the state is gone. The same holds for every kind
    that releases the GIL: an object, a handle module (and its
    re-`__init__`), a capsule module's `destroy`, and a composer's `close()`
    and its source's re-`__init__`; each now releases it through one
    emitter (gh-2267). A module object that gains a `nogil` method after its
    fragment was written has its teardown re-rendered by the verb. A
    fragment `apply` does not re-render -- one rendered by 0.105.0 -- is
    named by `apply` and `status` ("does not count the released call");
    delete it and re-run `apply` to pick the guard up.
