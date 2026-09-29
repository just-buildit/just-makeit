- **A C refusal's reason reaches the Python exception** (gh-1706). A module
    function declared `why = true` (`jm function --why`) takes a trailing
    `const char **why`; the binding passes it, and a `check_return` refusal
    raises `ValueError(<the reason>)` instead of `RuntimeError: <fn> failed`.
    A composer's delegated JSON reader does the same when its
    `[module.X.json]` table sets `from_json_why` / `from_file_why`, so
    `from_json` / `from_file` name what they refused (a retired key, a
    malformed field) rather than `<fn> failed`, and the generated C CLI
    prints it. A refusal that writes no reason keeps the old message. One
    emitter, `_context._diagnostics.reason_raise_c`, now renders every such
    raise, including the composer bridge's `bridge_error_fn` (gh-1307). The
    manifest writer behind `jm split-objects` now derives a module
    function's keys from the accepted set, so it no longer drops
    `check_return`, `why`, `impl`, `impl_file` or `replace`.
