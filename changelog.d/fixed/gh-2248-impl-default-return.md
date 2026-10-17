- **A `variable_output` `impl` with no `return` compiles in every shape**
    (gh-2248). jm completes such a body with a `return`, and it was always
    `return n;`, which names a parameter only the counted generator shape
    has: an input shape (`n_in`), an array param (`<p>_len`), an all-scalar
    shape and a `no_count` one (no count at all) failed to compile. The
    appended `return` now names the shape's own count, the one its
    `_max_out` takes, and a kernel with none returns its `_max_out(state)`,
    the bound the binding sized the output with, cast to the declared
    `count_type`.
