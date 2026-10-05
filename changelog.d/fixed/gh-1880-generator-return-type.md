- **A generator's manifest records the return type its header declares**
    (gh-1880). `jm object g --preset generator` (or `--arg-type void`)
    scaffolded a `step()` returning `float _Complex` but wrote
    `return_type = "void"` to the manifest, so `jm status` was STALE on the
    fresh scaffold and `jm apply` re-rendered the binding from the manifest
    and broke the build (`too few arguments to function '..._steps'`). The
    manifest writer, the manifest reader's default for an absent
    `return_type`, and `jm script` each kept their own copy of the default;
    all three now use the renderer's, so the manifest says `float _Complex`,
    a hand-written object table without `return_type` means what omitting
    `--return-type` means, and `jm script` still spells a deliberate
    `--arg-type void --return-type void`. A project scaffolded with the bug
    keeps `return_type = "void"`: set it to `"float _Complex"` in that
    object's table (`objects/<name>.toml`) before running `jm apply`.
