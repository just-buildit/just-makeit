- **A method `impl` with no `return` compiles in every shape** (gh-2248).
    jm completed such a body only for a `variable_output` method, always
    with `return n;`, a parameter only the counted generator shape has: an
    input shape (`n_in`), an array param (`<p>_len`), an all-scalar shape
    and a `no_count` one failed to compile. Every other shape got nothing
    and fell off the end of a non-void function, and so did a fresh stub
    for a fixed method returning a declared record by value. Now each
    shape gets its own neutral `return`: the count its `_max_out` takes (or
    `_max_out(state)` when it has none, cast to `count_type`), `0` records
    for a list of records, `NULL` for a borrow, the type's zero for a
    scalar, and a zeroed `(<T>){0}` for a record returned by value. A body
    counts as complete only when its last statement is a `return`, so one
    that returns early and then falls off its end is completed too.
