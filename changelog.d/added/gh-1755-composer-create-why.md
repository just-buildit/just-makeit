- **A composer's create can name its refusal** (gh-1755). A
    `kind = "composer"` module declared `create_why = true` has a create --
    its `create_fn`, or the `<backing>_create` default -- that takes a
    trailing `const char **why`, the shape of a JSON reader's
    `from_json_why`. Every face that builds a composer from segments passes
    the reason through: the `Composer([...])` constructor raises
    `ValueError(<the reason>)` instead of `<create> failed`, the generic
    `from_json` / `from_file` raise it instead of `invalid composer spec`,
    and the c-face CLI prints it instead of `failed to build composer`. A
    refusal that writes no reason keeps the old message, and a module
    without the key renders byte-identically. Like gh-1722's switches, a
    `create_why` that is not `true`/`false` is refused at load.
