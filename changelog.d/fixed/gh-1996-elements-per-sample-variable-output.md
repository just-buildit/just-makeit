- **`elements_per_sample` on a `variable_output` method's array is honoured,
    no longer accepted and ignored** (gh-1996). The key reached only the
    `<p>_len` local of the ordinary method-param builder, which the
    variable-output binding never used, so an interleaved `int16_t[]` I/Q
    kernel was handed its input and its `pass_capacity` capacity as element
    counts where it counts samples: it wrote twice as far as the buffer
    allows, and its result came back half as long as what it produced. Every
    count now crosses into C in samples and comes back as elements: the input,
    the `max_out` count, the capacity (allocated and `out=`), the trimmed
    result, and `<m>_max_out`, which still takes `len(x)` and answers in
    elements, so it sizes `out=` as before. An
    output whose element differs from the interleaved array's is refused
    before anything is written, as is an `elements_per_sample` that is not an
    integer of at least 1. `rank`, the key's sibling, was dropped on the same
    path and now guards it too.
