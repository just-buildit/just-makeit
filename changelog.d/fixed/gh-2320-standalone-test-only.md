- **A standalone object's `test_only` dependency reaches its C test
    and bench** (gh-2320). `{ name = "x", test_only = true }` (gh-537)
    linked `x_core` into a module object's test and bench but into
    neither for a standalone object: they shared the extension's link
    list, which leaves every `test_only` entry out. So a C test that
    round-trips through the sibling could not link. They now link the
    extension's libraries plus what the object's `test_only` edges reach.
    The extension itself still links none of it.
