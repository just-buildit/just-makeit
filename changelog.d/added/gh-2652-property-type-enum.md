- **`jm property --type enum:<name>` declares an enum-backed property.**
    The init-param spelling of an enum is now accepted on a property as a
    synonym for `--type int --enum <name>`: the property reads back as the
    `[[enum]]` choice string, and the manifest keeps the canonical
    `type = "int"` + `enum = "<name>"` row, so every file written is the
    same as the long form's (gh-2652).
