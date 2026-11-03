- **`just_makeit.is_versioned(path, root)` is public** (gh-2455). It says
    whether `jm status` compares a project file against jm's current
    render, so that a difference is reported `OUTDATED`. A gate on
    `jm status --check` passes on an empty `OUTDATED` list, which cannot
    tell "every compared file is current" from "jm stopped comparing this
    one"; asking per committed file tells them apart without importing the
    private `_createonly`. It is that function re-exported, not a copy, and
    a `str` root now works as well as a `Path`. A root holding no
    `just-makeit.toml` raises `FileNotFoundError`, as `load_manifest` does,
    rather than being answered from an empty manifest.
