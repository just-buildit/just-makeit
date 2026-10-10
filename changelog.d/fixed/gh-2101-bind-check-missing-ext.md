- **`jm bind <comp> --check` reports a binding that is not on disk, on one
    `error:` line naming the `_ext.c` `jm bind <comp>` would write** (gh-2101).
    It read the file without asking whether it was there, so a header not
    bound yet was a `FileNotFoundError` traceback. A stale binding's line
    now goes to stderr beside it, and names `jm bind <comp>` as the fix.
