- **A generated complex argument is spelled as `ruff format` leaves it**
    (gh-2513). The functions contract test passed a complex scalar as its
    repr, `(1+0j)`; ruff format rewrote it, and the next `jm apply` wrote it
    back. The argument is now `1 + 0j`, derived from the same table value
    by `ast.unparse`; the table keeps the repr, which generated doctests
    expect.
