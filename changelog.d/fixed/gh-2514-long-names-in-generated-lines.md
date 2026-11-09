- **A long component, module or app name no longer pushes a generated
    line past 79 columns** (gh-2514). The same names that overflowed the
    re-exports also overflowed other generated lines. These are a
    benchmark docstring's `Run` line, a module's `.pyi` and `__init__.py`
    header comments, and an app's docstring summary. They also include the
    constructor call in an app and in a test's `with` statement, and an
    app's `argparse` description. Past 79 columns, a header or summary now
    wraps at its spaces. A call puts one argument per line with a trailing
    comma. A description becomes a `" ".join([...])` of its words. The
    benchmark puts its path alone on its own line. Each is a layout ruff's
    formatter keeps at both 79 and its default 88. Short names render
    exactly as before.
