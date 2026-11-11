- **A renamed or removed `reexports` key takes its import with it**
    (gh-2608). Renaming `[module.X.reexports] cic_design` to `_cic_design`
    made `jm apply` add `from ._cic_design import ...` and keep
    `from .cic_design import ...`, so the package no longer imported, and
    `jm status --check` agreed with the apply that left it. Nothing in the
    statement tells jm's line from an author's (gh-342), so `apply` now
    records the submodules it re-exported from, as
    `[module.X] applied_reexports`, and on a later apply drops jm's
    statement for a submodule on that record that the manifest no longer
    declares. `status --check` reports the file `STALE` until it does. A
    stale line written before the record existed is never touched: delete
    it by hand, once.
- **A `reexports` key starting with `_` survives a manifest save**
    (gh-2608). jm's manifest writers took `_cic_design` for runtime state,
    so any command that rewrote the module's `reexports` table deleted it
    from the file. A key in that table is a submodule name, and every
    reader and writer of the manifest now asks one question,
    `_config.is_runtime_key`, about a `_` key.
