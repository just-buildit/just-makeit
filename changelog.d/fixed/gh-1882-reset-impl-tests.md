- **A component whose `reset()` is the author's passes its own generated
    tests** (gh-1882). A `reset_impl` that keeps a field -- the `delay_line`
    README's own fragment, which zeroes the ring buffer and keeps `length`
    -- scaffolded a project that failed on the first `make test`: the C
    test, the Python `test_reset` and the `.pyi` doctest each asserted that
    `reset()` restores every declared default, and none of them asked
    whether jm wrote that `reset()`. One predicate now decides, read by all
    of them and by both `.pyi` generators, the runtime class docstring and
    `jm bind`: a `reset_impl` / `reset_impl_file` (or `--impl reset::...`),
    `init_params` -- whose author-written `create()` defines the
    post-create state `reset()` returns to, so the generated `test_reset`
    under `init_params` now only calls it -- or `no_reset`. Where it holds,
    both tests call `reset()` and assert nothing about the result, and the
    doctest leaves its "Reset restores defaults" demo out. `jm apply` carries
    the declaration into its replay, so an object that also has a method,
    or sits in a module, keeps the answer when its `.pyi` is re-rendered;
    `jm bind`, which has no manifest, reads it off the `reset()` body. The
    `delay_line` example no longer overwrites its generated tests.
