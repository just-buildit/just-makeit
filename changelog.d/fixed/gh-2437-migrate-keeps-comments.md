- **`jm migrate-to-fragments` and `jm split-objects` keep every comment**
    (gh-2437). Both rebuilt each file they wrote from its values, so every
    comment in the manifest was deleted: all of doppler's, the ones on
    `[project]` keys included, and none reached `modules/*.toml`. Now the
    manifest is cut, not rebuilt. Each moved table takes its own comments
    to its fragment, byte for byte: the ones inside it, on its sub-tables
    and table-array rows, and the run of comment lines directly above its
    header. The root file is the original minus the moved tables, every
    other comment and blank line intact. A new `include` goes after the
    file's opening comment block, and an existing one is extended in place.
