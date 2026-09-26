- **`jm upgrade` respells a manifest's `replace` table** (gh-1656). Its
    values are C spliced into the `impl` body and its keys are matched
    against it, but neither sat behind a key the `c_prefix` respell knew, so
    a value naming a derived symbol kept the old spelling (the next render
    from the manifest did not compile) and a key over a respelled
    `impl_file` body silently stopped matching. Both sides, inline or as a
    `[<comp>.replace]` table, are now respelled in place and named by
    `apply`'s existing-tree refusal, through one reading,
    `_csym.manifest_c_spans`. A key whose body comes from a file the upgrade
    does not respell keeps its spelling.
