- **A `--class-name` object keeps its name through `jm apply`** (gh-1651).
    On a standalone object whose header is documented, `apply` re-rendered
    the binding under the default name -- its `tp_name`, its
    `PyModule_AddObject` and its `.pyi` class -- while `__init__.py` still
    imported the declared one, so the package did not import; `status --check`
    agreed with the replay that produced it. A fresh `--class-name` scaffold
    was also STALE against itself. The class name now comes from one place
    (`_config.resolved_class_name`, seeded in every render context), and jm's
    own `Reset <Class> ...` boilerplate is recognised under a declared class
    name. For a module object with a `class_name`, `reset`'s docstring is
    now jm's generic one rather than the header's unedited template line.
