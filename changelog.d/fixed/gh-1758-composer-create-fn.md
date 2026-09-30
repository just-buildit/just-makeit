- **A composer honours `create_fn`** (gh-1758). The key was accepted on a
    `kind = "composer"` module and read by nothing: every face called
    `<backing>_create`. It now names the create the `Composer` constructor,
    the generic `from_json` / `from_file` and the c-face CLI all call,
    exactly as written (an author-named key, never respelled by a
    `c_prefix`); unset, the `<backing>_create` default is unchanged.
