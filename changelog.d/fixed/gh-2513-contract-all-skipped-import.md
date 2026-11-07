- **A functions contract test whose every case is skipped imports nothing
    unused** (gh-2513). With no wrapper called, `import <pkg>.<mod> as module`
    was ruff F401; `ruff --fix` deleted it and the next `jm apply` wrote it
    back, so the tree could not be green without a hand edit. The import is
    now written only when at least one case calls the module.
