- **A list-of-records module function, and the generated benchmark of a
    list-of-records method, size the result from a declared capacity**
    (gh-2205). gh-2184 left two faces handing the C a fixed `max_results`
    (64): a `jm function --result-field` binding mallocked 64 records even
    when `out_size` was declared, so a call due more returned the first 64,
    and the benchmark timed a kernel that stops at a full buffer over 64
    records rather than its whole call. A records function now sizes its
    buffer from `out_size` -- the capacity key a `variable_output` function
    already declares, a C expression over its params -- and hands the C
    that count; `jm function --out-size` is accepted with `--result-field`.
    Undeclared, `max_results` stands. `out_size` beside
    `max_results_param`, two capacities for one buffer, is refused. The
    benchmark asks the method's header-declared `_max_out()` when the header
    declares it at render time and allocates that many records; it stays
    create-only, so delete it and re-run `apply` to pick a later
    declaration up.
