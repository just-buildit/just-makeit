- **`consumed_fn`: a list-of-records method re-offers the input its kernel
    left** (gh-2204). A kernel whose output cannot be bounded before the call
    -- a detector's hit count depends on the data -- stops when its `result`
    buffer is full, and the binding called it once: `push(np.ones(100))`
    into a kernel that stops at 64 returned 64 records, and elements 64..99
    were never seen by the C. Declare `consumed_fn = "<fn>"` (or
    `jm method --consumed-fn <fn>`), naming the author's
    `size_t <fn>(const <state> *)` -- the input the last call took -- and
    the binding calls the kernel, appends its records, and offers it the
    rest of the input block until none is left, returning every record in
    one list, in call order, as before. Params ride along unchanged on every
    call. With a header-declared `_max_out()` as well (gh-2184), each call
    is sized from it for the input still left. A call that takes none of
    the input left raises `RuntimeError` naming the method rather than
    spinning, and so does one claiming more than it was given. The name is
    the author's and spelled as written under a `c_prefix`. The key is
    refused at load, and by `jm method` before it writes, on a method with
    no array input or one that does not return a list of records. A module
    object's fragment rendered before the key is declared is reported by
    `apply`; delete it and re-run `apply` to pick the loop up.
