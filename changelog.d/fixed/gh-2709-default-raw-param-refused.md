- **`default_raw` on a parameter that does not read it is refused at load
    (gh-2709).** It is an init-param's key; a method's, a module function's
    or a handle / capsule table's parameter takes a named C constant as
    `default` itself. Written on one, it drew an advisory warning, exited 0
    and was ignored, so the parameter rendered with no default: an optional
    argument turned required on re-render. The refusal names the row, the key
    and the spelling that works, `default = "<C constant>"`. The key walk
    also reaches a view's method rows and their params, which it skipped.
