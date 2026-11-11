- **A generated project's Python versions live in its own `pyproject.toml`**
    (gh-2629). The CI `jm ci` writes hard-coded its `python-version` matrix
    in jm's template, and the generated `pyproject.toml` declared no version
    classifiers, so a project could not drop a Python without hand-editing
    a workflow `jm ci --force` would put back. `jm new` now writes
    `Programming Language :: Python :: 3.N` classifiers, and
    `requires-python`, from jm's own installed metadata; the GitHub
    workflow's matrix is rendered from the project's classifiers, and every
    `jm apply` rewrites that one list to match them, so `jm status --check`
    reports the workflow STALE until it does. The workflow stays static
    YAML. A project with no version classifiers keeps its `pyproject.toml`
    untouched and its CI follows jm's list, with a one-line note saying to
    add them.
