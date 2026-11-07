- **A generated complex argument is spelled as `ruff format` leaves it**
    (gh-2513). The complex types' `py_one` was Python's repr, `(1+0j)`;
    ruff format rewrote it to `(1 + 0j)` in the functions contract test,
    and the next `jm apply` wrote it back. It is now `(1 + 0j)` at its
    one definition in `_types`, for every reader.
