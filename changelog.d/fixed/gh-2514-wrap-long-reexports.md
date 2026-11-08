- **A long re-export in an `__init__.py` is wrapped at 79 columns**
    (gh-2514). jm wrote a module's `from .<leaf> import ...  # noqa: E402`
    and its `__all__` on one line whatever their length, so a module
    re-exporting `gauss_hermite_quadrature` and three more names got lines
    of 130 columns: E501 at 79, a `ruff format` rewrite even at ruff's 88,
    and every `jm apply` undid the formatter's wrap. Past 79 columns jm
    now writes ruff's own form: one name per line, a trailing comma, and
    the `# noqa: E402` after the `(`. Both `ruff check --select I --fix`
    and `ruff format` keep it at any line length. Below 79 columns the
    single line is unchanged. The same holds for a package's standalone
    re-exports and `__all__`, and for a platform-guarded import, which is
    indented line by line. An import that `ruff format` wrapped, with the
    marker after the `)`, reads back to jm's form. An existing project's
    long lines are wrapped on its next `jm apply`.
