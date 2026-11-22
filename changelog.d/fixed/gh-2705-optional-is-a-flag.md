- **An init param's `optional = "false"` means false (gh-2705).** Every
    reader tested the key for truth, and the TOML string `"false"` is truthy,
    so on an array init param it turned array dispatch ON and `apply` refused
    the row for naming no `create_fn`. The key is now read through the one
    reading of jm's string-typed flags: a TOML bool or `"true"` / `"false"`,
    each meaning what it says. Any other value is refused at load, naming the
    table, the row and the key.
