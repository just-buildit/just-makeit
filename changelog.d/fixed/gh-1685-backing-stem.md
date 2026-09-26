- **A capsule or composer `backing` that names a jm component follows its
    prefix** (gh-1685). The glue spelled the backing API by hand
    (`<backing>_create`, `_destroy`, `_reset`, `_state_t`, ...), so after
    `c_prefix` + `jm upgrade` a capsule over component `lo` still called
    `lo_create` while the header declared `dp_lo_create`, and the build
    failed. A `backing` naming a component now spells those C symbols
    through the component's stem; one naming a hand-written core is used
    exactly as written. The header path, capsule name and Python function
    names are unchanged. `jm status` (and `--json`, as `backings`) says
    which reading each `backing` took.
