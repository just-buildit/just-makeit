- **A `batch` method has a `.pyi` member and an array-typed `__doc__`, and
    `nogil` releases the GIL on every method whose binding jm writes**
    (gh-2259). A `batch` method (`jm method --batch`) is jm's elementwise
    block method: an array in, an array of the same length out, an optional
    `out=`. The standalone `.pyi` had no member for it, the module one typed
    it `prod(self, x: complex) -> complex`, and the runtime `__doc__` said
    `x : complex` and `Returns complex`. Both stubs and the `__doc__` now
    read `prod(x, out=None) -> NDArray[...]`, or `fill(count=1, out=None)`
    with no input, and agree. Their sections come from the same builder.
    `nogil = true` was accepted and ignored on a `batch` method, and on
    every other shape that spelled its kernel call inline: a scalar,
    `void`, `status_return`, `error_negative`, `multi_output` or `out_type`
    result, a codec-pack method's sink, and a capsule module's bare
    `(state)` method. doppler had nine such methods that it believed
    released the GIL. Every call jm writes now goes through one emitter, so
    the GIL is released on both of a batch method's paths, with the arrays
    acquired and the result allocated first. A method without `nogil`
    generates the same code as before. A `varargs` or `manual_stub` method
    has no generated call, so jm refuses `nogil` there and asks you to
    release the GIL in your own binding. A module object's fragment
    rendered before this fix keeps its old call, and `jm apply` and
    `jm status` name each `nogil` member in it that holds the GIL.
