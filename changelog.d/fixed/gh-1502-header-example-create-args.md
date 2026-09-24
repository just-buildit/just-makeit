- **The `_core.h` example no longer hard-codes scaffold-time `create()`
    values, and `jm status` flags one that falls behind** (gh-1502). The
    `@code` example at the top of a new object's header now declares one
    local per constructor parameter, with the prototype's type, and passes
    them by name (`int level = 0; // your value` then `o_create(level)`).
    It used to hard-code the scaffold's values. The header is yours, so
    `jm apply` still never rewrites the example. After init params change,
    `jm status` prints an advisory `EXAMPLE` section naming the line whose
    `create()` call has a different argument count from the prototype. It
    also appears as `create_example_drift` in `--json`. It does not fail
    `--check`.
