- **A functions-only module gets a Python contract test, so a broken wrapper
    fails `jm test`** (gh-2127). Such a module built an extension and generated
    no Python test that called it, so a wrapper with the wrong return type or a
    parameter the binding did not parse passed `jm build` and `jm test` both. Its
    `tests/test_<module>_functions.py` now calls each wrapper with arguments
    derived from its declared parameters and asserts the call succeeds and the
    result has the declared return type. It asserts no values: the bodies are the
    author's stubs, and their documented cases are the header's `@code` examples.
    A function whose shape the contract cannot call yet (an out parameter, a
    string, a record result) is a named skip, not a silent pass. The file follows
    the module: adding a function rewrites it, and an object added to the module
    removes it, as does removing the last object's function. A file the author has
    taken over (the ownership token deleted) is never removed.
