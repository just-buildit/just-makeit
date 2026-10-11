- **An object that hands out views no longer frees the memory under them
    at `destroy()`** (gh-2187). A `borrow = true` method, a `buf_field`
    property and an array state's `get_<name>_view()` each pin the object,
    not its memory, so an explicit `destroy()` or `__exit__` left every
    outstanding view, and a GIL-free call still running, reading freed
    memory. Such an object's teardown now parks the state and every method
    refuses as before; `tp_dealloc` frees it once the last view and call
    have let go. `[<obj>.destroy] wake = "<method>"` names a method to call
    first, so a blocking call returns. A fallible destroy on a lending
    object is refused at generation: a finalizer named by `exit` reports
    the status instead. A module object that starts or stops lending after
    its fragment was written has the teardown jm wrote re-rendered with
    it, and one the author edited is kept and named by `apply` and
    `status`.
