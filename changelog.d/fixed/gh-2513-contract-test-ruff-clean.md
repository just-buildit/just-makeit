- **The functions contract test and a functions-only module's `.pyi` are
    clean under ruff at 79 columns** (gh-2513). A wrapper call too long for
    one line is now written one argument per line with a trailing comma, a
    layout `ruff format` keeps at any `line-length`. A functions-only stub
    separates its imports from the first `def`. Before this, doppler's
    generated contract tests had 29 lines too long, and `ruff --fix`
    rewrote jm-owned files.
