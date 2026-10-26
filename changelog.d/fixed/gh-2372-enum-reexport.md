- **A module's published enum is importable from its package** (gh-2372).
    gh-2344 added each indexed `[[enum]]` to the extension module, and the
    `.pyi` declared it, but the package `__init__.py` re-exported only the
    types and functions. So `pkg.mod.mod.NAME` worked and `pkg.mod.NAME`
    raised `AttributeError`. The re-export now reads the same list the
    extension publishes, and `apply` adds it to an existing `__init__.py`.
