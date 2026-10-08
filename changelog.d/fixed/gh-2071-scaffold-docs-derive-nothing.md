- **`jm method --fn` and `jm object --step-delegates-to-steps` leave the
    tree `jm apply` writes, because jm reads its own scaffold Doxygen as
    jm's** (gh-2071). Both verbs write a doc block into the header that only
    jm wrote, and the one test that decides whether a block is jm's
    boilerplate or the author's prose missed both. An `fn`-overridden
    method's skeleton (`@brief m2.` above `o_custom_m2`) was judged against
    the symbol rather than the method's name, and the delegating `step()`'s
    note (`Thin delegator to <csym>_steps() ... (gh-208).`) counted as
    authored body prose. So `apply` derived them into the binding and the
    `.pyi` -- `m2.` for `M2.`, and jm's own note in `step()`'s docstring --
    while the verb had rendered the name fallback, and `status --check`
    called the glue STALE straight after the verb. The block's member is
    now read through the manifest's method-to-symbol map, the map the
    skeleton is stamped from, and the note has one definition, which the
    header is written from and the test recognises. A project whose
    `step()` docstring carries the note loses it on the next `apply`.
