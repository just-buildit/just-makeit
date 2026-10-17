- **`migrate-to-fragments` names a dotted module's fragment as `jm module`
    does** (gh-2306). It wrote `[module."dsp.filters"]` to
    `modules/dsp.filters.toml`, while `jm module` and every mutating command
    write `modules/dsp_filters.toml`, so one manifest gave differently named
    fragments depending on how the project got there. Both now spell the
    path through one helper, `modules/<cname>.toml`.
