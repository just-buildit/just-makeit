- **A manifest reorder your C definition does not share is refused, not
    swapped silently** (gh-2465). Swapping two same-typed `[[x.state]]`,
    `[[x.init_params]]`, method `params` or module function `params`
    entries made `apply` rewrite the prototype in `_core.h` (or, for an
    `inline` function, the binding's call) while the definition kept the
    old order. C compiled it, `status --check` exited 0, and every caller
    following the header passed the two values swapped. `apply` now
    refuses before writing anything, naming the definition's `file:line`
    and both orders; `status` reports the same finding as `ORDER`, which
    gates and is never suppressed, including on a tree an older jm already
    applied. jm never edits the definition: reorder its parameters and the
    call sites, or restore the manifest's order. A header `@code` example
    passing the parameter names in another order is an advisory `EXAMPLE`.
    `docs/configuration.md` "Editing TOML by hand" now lists where order
    matters in `just-makeit.toml`.
