- **A project straight from `jm new` passes its own `make test`**
    (gh-2640). With no component yet the project had no Python test, and
    unittest on Python 3.12+ exits 5 when it runs none, so `make test`
    failed on a tree nobody had touched. `jm new` now writes
    `src/<pkg>/tests/test_project.py`, which checks that the package
    imports and that its new `__version__` is the manifest's
    `[project] version`. Both copies follow `jm config version` and are
    reported by `jm status`; `jm apply` adds the test to an existing
    project, and a `__version__` line to a package `__init__.py` that
    names none. The `--pytest` recipe no longer passes pytest's exit 5
    (no tests collected): an empty run proves nothing.
- **`jm object project` is refused** (gh-2640). A component is the
    manifest table of its own name, so one named `project` -- or any other
    reserved top-level key -- replaced `[project]` with the object's keys
    and exited 0.
