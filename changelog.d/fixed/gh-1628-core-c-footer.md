- **`jm status` mentions `_core.c` only when one is stale** (gh-1628). The
    closing "Your `_core.c` is yours -- apply only ADDS ..." sentence ended
    every drift report, so a tree whose only drift was a regenerated glue
    file finished with a sentence about a file the report did not name. It
    is now printed by the same list as the "STALE -- yours" section.
