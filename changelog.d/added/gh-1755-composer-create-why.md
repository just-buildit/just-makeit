- **A composer's create can name its refusal** (gh-1755). A
    `kind = "composer"` module declared `create_why = true` is built by the
    backing's reason-naming constructor,
    `<backing>_create_why(segs, n, repeat, continuous, const char **why)`,
    which the backing provides beside its plain `<backing>_create`. Every
    face that builds a composer from segments passes the reason through: the
    `Composer([...])` constructor raises `ValueError(<the reason>)` instead
    of `<backing>_create failed`, the generic `from_json` / `from_file` raise
    it instead of `invalid composer spec`, and the c-face CLI prints it
    instead of `failed to build composer`. A refusal that writes no reason
    keeps the old message, and a module without the key renders
    byte-identically. Like gh-1722's switches, a `create_why` that is not
    `true`/`false` is refused at load.
