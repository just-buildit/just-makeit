- **A composer's `[module.X.json] header` and `include_dir` are accepted and
    kept** (gh-1725). The generated JSON path includes the named cJSON header
    and adds `include_dir` to the extension's include path, as documented,
    but the key check did not know either key: a project naming its vendored
    cJSON got the right binding and a load-time warning that the key "has no
    effect". A mutating save also dropped both, which put the default
    `cJSON.h` back in the next generated `_ext.c`. The test that checks every
    key the composer renderer reads against the accepted keys now covers every
    composer sub-table and every renderer, not two tables and three renderers.
