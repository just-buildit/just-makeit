- **Two keys naming one composer seam function must agree on its
    prototype** (gh-1739). The bridge header declares each straight-C seam
    once per function, taking the first key's prototype. Two owned-pointer
    source fields naming one `parse_fn` with `parse_why` on only one of them,
    or one `copy_fn` / `free_fn` / `format_fn` over two `type`s, were
    accepted, and the call site that did not match the one declaration
    failed in the C compiler with conflicting types. jm now refuses it when
    it renders the bridge header, naming the function, both keys (fields
    included) and both prototypes. The rule covers every seam the header
    declares, so a `coerce_str_fn` that collides with an owned pointer's
    function is refused the same way. Keys that agree are still declared
    once.
