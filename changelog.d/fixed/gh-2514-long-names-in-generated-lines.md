- **A long component, module, function or app name no longer pushes a
    generated line past 79 columns** (gh-2514). The same names that
    overflowed the re-exports also overflowed lines in tests, benchmarks,
    stubs, a module's `__init__.py` and the Python app faces.

    - Headers and summaries: a benchmark docstring's `Run` line, a module's
        `.pyi` and `__init__.py` header comments, and an app's docstring
        summary.
    - Calls: the constructor call in tests, benchmarks and apps, and a
        function app's call.
    - Imports from the project's own package: in tests, benchmarks, `.pyi`
        doctests and apps.
    - An app's `argparse` description.

    Each site now writes its own line already held to 79 columns:

    - A header or summary wraps at its spaces.
    - A call puts one argument per line with a trailing comma.
    - An import uses the parenthesised form the re-exports use.
    - A description becomes a `" ".join([...])` of its words.
    - The benchmark puts its path alone on its own line.

    Each of these layouts is one ruff's formatter keeps at both 79 and its
    default 88. A multi-command app now puts `prog` and `description` on
    lines of their own, which stopped at 87 to 90 columns. Short names
    render as before. An existing project's long lines are rewritten on its
    next `jm apply`. A module `__init__.py` header, which jm writes only
    once, is also rewritten, but only when the line is exactly the one jm
    wrote. An author's own comments are never touched.
