- **A count the kernel returns past its buffer raises `RuntimeError`**
    (gh-1716). A self-sizing output hands its kernel a buffer of known
    capacity and then shapes the result by the count the kernel returns.
    Nothing compared the two, so a kernel returning more than it was given (a
    bug, or a `(size_t)-1` sentinel) produced a result shaped past its own
    allocation: a module function's array read past its buffer, a
    `variable_output` method's `PyArray_Resize` grew the result into memory
    the kernel never wrote, and its `out=` view ran past the caller's array.
    Every such site now raises
    `RuntimeError("<name>: wrote <n> elements into a buffer of <cap>")`
    before the count is used. That covers a module function's ndarray,
    `str` and list-of-records outputs, a method's allocated and `out=`
    paths (`record_dtype` included) and its list-of-records, a handle's
    `out_len_fn` array and `bytes`, its int-in array and its `out[:n]` view,
    a capsule's `execute`, and a composer's `execute` and `compose`. The
    `str` output used to clamp the count silently; it raises too, because a
    count past the buffer means the kernel already wrote past it. A count
    up to the capacity, zero included, is returned as before. `jm apply`
    and `jm status` report a sacred fragment rendered before the guard.
