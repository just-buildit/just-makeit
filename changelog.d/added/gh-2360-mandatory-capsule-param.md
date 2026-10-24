- **A method capsule param can be declared mandatory** (gh-2360).
    `required = true` on a `[[<object>.methods.params]]` capsule param makes
    the binding raise `TypeError` for `None` before the kernel runs, and types
    the param `object` in the `.pyi` and the docstrings rather than
    `object | None`. Without the key, `None` still passes as `NULL` (the
    detach idiom). A module fragment rendered before the key is named by
    `apply`.
