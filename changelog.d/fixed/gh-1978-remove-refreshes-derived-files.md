- **`jm remove` leaves the tree its manifest describes: the link-check
    table and the element contract follow the remove** (gh-1978).
    `jm remove method`, `property`, `warning` or `error` on a standalone
    object re-rendered the binding and the stub but not
    `native/tests/test_<obj>_symbols.c`, so a removed method's or
    property's symbol stayed in it: `jm status --check` reported it STALE
    and exited 1 on the tree the remove had just written, and once you
    deleted the body as the remove's note says, the C test failed to link
    with `undefined reference`. The remove now re-renders through the same
    call `jm property`, `jm warning` and `jm error` use, which writes the
    table. A module object was not affected. The same sweep found
    `test_<obj>_invariants.py` (the gh-1404 element contract) outliving its
    pair: `jm remove method` of the pair's writer or reader now deletes it,
    and `jm remove object` takes it with the object -- left behind, it
    imported the removed class and the project's Python tests failed to
    collect, while `status --check` exited 0.
