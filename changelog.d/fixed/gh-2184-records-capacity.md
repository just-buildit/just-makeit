- **A list-of-records method returns every record its C reports when the
    header declares the method's `_max_out()`** (gh-2184). The binding
    called the kernel with a fixed stack array, `rec_t results[64]`, and
    the kernel's `(…, rec_t *result, size_t max_results) -> count` contract
    has no way to say there were more, so a call due 100 records returned
    64 and dropped the rest without a word. The only lever was a bigger
    `max_results`, a bigger stack array on every call. Now a header that
    declares `<pkg>_<comp>_<name>_max_out(state, n_in)` -- the capacity
    function a `variable_output` method already has, with the same name
    and arity rules (`n_in`, `<param>_len`, or state-only) -- is asked on
    every call; the binding allocates that many records, hands the kernel
    the same capacity as `max_results`, frees the buffer on every way out,
    and returns them all. Standalone and module objects alike, for an
    input, params, or neither. Without a declaration the fixed buffer is
    byte-identical to before. A module object's fragment rendered before
    the declaration is not re-rendered by `apply`, which now reports that
    method as dropping records past `max_results`; delete the fragment and
    re-run `apply` to pick the capacity up.
