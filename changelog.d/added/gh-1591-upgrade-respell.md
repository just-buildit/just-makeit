- **`jm upgrade` moves an existing project onto its `c_prefix`** (gh-1591,
    phase 3). Set `[project] c_prefix`, run `jm upgrade`, then `jm apply`:
    every C symbol jm derives is respelled in your own C -- the sacred
    `_core.h` / `_core.c`, module function sources, tests, benchmarks,
    `native/examples/` -- in code only and as whole, case-sensitive
    identifiers, so comments, strings, your own macros and nested projects
    are left as written. It prints each file it changed and the rename table
    as `old<TAB>new` lines, for code jm does not own to follow, and a second
    run changes nothing. A prefix changed or removed after it was applied is
    refused, not migrated (gh-1650).
