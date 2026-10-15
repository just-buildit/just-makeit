- **A composer segment with no fields builds under `-Werror`**
    (gh-2264). A `kind = "composer"` segment's `__init__` and `sum`
    always ended in a `fail:` label for a field's parse to jump to, so
    a segment declaring no `fields` left it unreached: `-Wunused-label`
    in glue `jm apply` regenerates, a hard error under `-Werror`. Each
    function now carries that tail only when its own field parsing can
    reach it.
