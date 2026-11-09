- **A header `@param` documents a Python argument by name only, and a
    comma-grouped `@param a, b, c` documents each name** (gh-2223). The
    docstring renderer matched `@param`s to Python arguments by name, then
    paired the leftovers by position whenever their counts agreed. So
    documenting C-only parameters (`out1`, `max_out`, `status`, `why`)
    could give `x` the text "Output buffer." and `L` the text "Second
    output." in the `.pyi` stubs and the runtime `__doc__`. doppler hit
    this while adopting v0.126.0. Doxygen's grouped
    `@param state, x, x_len, L As fold().` was also dropped entirely,
    which left the real arguments over for that pairing. Now an argument
    reads the `@param` of its own name, or `@param in` for the block input
    `x` (jm's own C name for it), or the `Input.` placeholder. A comma
    group, with or without spaces and after an optional `[in]`, documents
    every name it lists. `create()`'s `@param` reconciliation reads the
    same grammar, so it keeps a grouped line once instead of adding bare
    duplicates. **A header that documented the input under another C name
    (`@param samples` for `x`) now gets `Input.`: name it `in` or `x`.**
