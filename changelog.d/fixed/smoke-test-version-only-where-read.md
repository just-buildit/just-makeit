- **The smoke test writes `MANIFEST_VERSION` only where a test reads it.**
    A package that computes `__version__` (`importlib.metadata`, a
    `_version.py`) gets a version check that reads no manifest value, yet
    `test_project.py` still carried the literal, and `status --check` held
    it to the manifest: a release bump made by the project's own tooling
    rather than `jm config version` went red on a line nothing used. The
    next `apply` drops it; a literal `__version__` keeps it, unchanged.
