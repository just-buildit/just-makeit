- **A method or module-function array param with `default = "[]"` no
    longer segfaults when omitted.** Omitted, or passed `None`, it reaches the
    C call as `NULL` with length `0`, as an init-param's has since gh-611, and
    an object method's `.pyi` marks it optional. An array default other than
    `"[]"`, and any default on a `path` or `bytes` param (which crashed the
    renderer with `KeyError`), are refused at load, naming the parameter
    (gh-2657).
