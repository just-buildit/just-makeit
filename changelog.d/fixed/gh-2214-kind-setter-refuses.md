- **A handle field's, capsule property's or composer setting's setter can
    refuse a value** (gh-2214). Each called the author's C setter and
    discarded what it returned, so assigning a value the core refused
    succeeded in Python while the attribute kept its old value. Each row
    now takes an object property's pair (gh-2182): with
    `error = "<Exception>"` (and optionally `error_message`) the setter --
    a getter field's `writable_fn`, a capsule `<backing>_set_<prop>`, a
    composer `setter_fn` -- returns `int`, and a non-zero return raises it,
    with the return code appended. A composer setting passed to the
    constructor is refused the same way. Both docstring faces document the
    exception. `error` where there is no setter, an unknown exception, or
    `error_message` alone is refused before anything is written. A row
    declaring neither key renders as before.
