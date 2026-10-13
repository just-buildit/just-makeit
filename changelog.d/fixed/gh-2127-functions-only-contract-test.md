- **A module with functions gets a Python contract test, so a broken wrapper
    fails `jm test`** (gh-2127). Such a module built an extension and generated
    no Python test that called it, so a wrapper with the wrong return type or a
    parameter the binding did not parse passed `jm build` and `jm test` both. Its
    `tests/test_<module>_functions.py` now calls each wrapper with arguments
    derived from its declared parameters and asserts the call succeeds and the
    result has the declared return type. It asserts no values: the bodies are the
    author's stubs, and their documented cases are the header's `@code` examples.
    An out array is passed as a writable buffer, which is what the binding fills.
    A function whose shape the contract cannot call yet is a named skip, not a
    silent pass: an out scalar, a string parameter or return, an array return, an
    enum-typed parameter, and a result shaped by `result_fields`, `out_type`,
    `variable_output`, `why`, `max_results_param` or `out_size` (tracked in #2160).
    The file follows the module's functions: adding a function rewrites it, and
    removing the last one removes it. A module with objects gets it too (#2156). A
    file the author has taken over (the ownership token deleted) is never written
    or removed again.
    An existing project with a module that declares functions gains
    `tests/test_<module>_functions.py` on its next `jm apply`. That is why the
    `stale_project` example's upgrade golden moved.
