- **`jm migrate-to-fragments` writes one `include`** (gh-2263). A
    manifest that already had `include` beside an inline object or
    module section was migrated by carrying the old `include` over and
    prepending a merged one, so the manifest said `include` twice and
    the migration failed reading it back (`Cannot overwrite a value`).
    It now writes the merged list once, and adds a directory's glob only
    when no existing entry already reaches the fragments it wrote --
    asked of the same include resolution `load` uses, so `*/*.toml` or
    an explicit path is not doubled under another spelling.
