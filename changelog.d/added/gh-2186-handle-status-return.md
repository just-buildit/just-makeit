- **A handle method with an array argument can declare its `int` a
    status, so `error` applies to it** (gh-2186). jm reads such a return as
    a count, samples taken, and refused `error` there (gh-1118), with no way
    to say a call such as `send(iq, fs, fc)` returns 0 for a taken block and
    non-zero for a refused one. `status_return = true` on the method says so:
    it is the object face's key, with its meaning. A non-zero return raises
    the method's `error` (`ValueError` when none is named, with
    `error_message` as on any status method), the binding returns `None`,
    and the `.pyi` says `-> None` with a `Raises` section. The refusal of a
    bare `error` on such a method now names `status_return = true`, and the
    key is refused over a non-integer `returns` or an array or `bytes`
    result, whose return is the payload length.
