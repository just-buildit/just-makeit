- **A module that declares `package` has its classes imported from that
    package, everywhere jm names one** (gh-2054). With `package = "other"` in
    `[module.mod]`, the class is `p.other.O`: the package's `__init__.py`
    re-exports it. But jm spelled the module id's path wherever it named the
    class, so the `.pyi` and runtime `__doc__` doctests said
    `from p.mod import O`, the element contract was written to
    `src/p/mod/tests/` and imported from there, an `object` reference's
    `.pyi` said `from .mod import O`, the `pep723` app imported from
    `p.mod`, and the `console` app was written to `src/p/mod/cli.py` --
    each naming a module that does not hold the class, on a tree
    `jm status --check` passed. `_config.module_package_resolved` is now
    the one answer to where a module's class lives, every one of those
    reads it, and the contract is written beside `test_<obj>.py`. A
    project that ran `jm apply` before this keeps the old contract at
    `src/<pkg>/<module>/tests/test_<obj>_invariants.py` (and a console app
    its old `cli.py`); both already failed to import, and are safe to
    delete.
