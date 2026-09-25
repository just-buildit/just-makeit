- **`just-makeit example` no longer fails on Windows after the example
    passes** (gh-1377). An example that imports the extension it built --
    `nco_tone`, once Windows builds it -- left a loaded `.pyd` that
    `TemporaryDirectory`'s cleanup could not delete, so the command raised
    `PermissionError` after printing `PASSED`. Every bundled example now
    builds into `_example.scratch_dir()`, whose cleanup is best effort.
