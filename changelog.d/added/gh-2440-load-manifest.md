- **`just_makeit.load_manifest(root)` and `just-makeit manifest --json`
    read the merged manifest** (gh-2440). Under the fragment layout every
    `[<obj>]` and `[module.X]` table lives in a file `include` names, so a
    script parsing `just-makeit.toml` with `tomllib` found no modules and
    could pass on nothing; the only reader that merges the fragments was the
    private `_config.load`. `load_manifest` is now the public, documented
    form of that same reader -- the manifest `jm apply` renders, templates
    and `init_groups` expanded -- returning a dict the caller may change,
    and raising `FileNotFoundError` for a directory with no manifest and
    `ValueError` for one jm refuses. `just-makeit manifest --json` prints
    the same dict for tools that are not Python. `migrate-to-fragments` and
    `split-objects` end with a note pointing such scripts at both.
