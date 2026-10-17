- **A count-in, array-out handle method passes every argument to C**
    (gh-2211). A `kind = "handle"` method returning an array with no array
    argument parsed its count and nothing else, so a declared `gain` after
    it was accepted at the call and never reached C. Every argument after
    the count now passes through behind it, `fn(h, out, n, gain)`, with
    keywords and defaults as an array-in method's trailing scalars have, and
    the `.pyi` names them all. A `string` argument, which only the two
    `out_len_fn` shapes accepted, is now a borrowed `const char *` on every
    method shape; a `string` or enum count is refused naming it.
