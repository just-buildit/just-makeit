- **An `extra_methods` row no longer breaks the C test's symbol table, and
    the core calls it makes are link-checked** (gh-2175). jm renders the
    row as `(PyCFunction)(void (*) (void))<fn>`, and the gh-1361 scanner
    read `void (` as a call. A header with any callback member, such as
    `void (*cb)(void *)`, supplied the other half, so `apply` wrote
    `(jm_any_fn)void,` into `test_<obj>_symbols.c` and the C test did not
    compile. The scanner now drops every C keyword, C99 through C23, not
    only the ones seen so far. The table is also read from the object's
    `_extra.c` hook as well as its binding, so a core function only the
    hook calls is still checked at link time.
