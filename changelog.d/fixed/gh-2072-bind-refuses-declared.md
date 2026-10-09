- **`jm bind` refuses a component the manifest declares, naming
    `jm regenerate <comp>` and `jm apply`, and writes nothing** (gh-2072).
    `bind` renders `<comp>_ext.c` and the `.pyi` from the header alone. On a
    declared component that was a second render of a binding `jm apply`
    already owns, and a lossy one: a warning, a `create()` error or a record
    type is nothing a header says, so `bind` dropped it, `jm status --check`
    reported both files STALE, and the next `apply` rewrote them.
    `jm bind --check` refuses the same way, from the same check. A header
    the manifest does not declare -- what `bind` is for -- binds as before.
