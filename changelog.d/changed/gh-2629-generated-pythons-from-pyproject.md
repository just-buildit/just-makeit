- **A generated project's Python versions live in its own `pyproject.toml`**
    (gh-2629). The CI `jm ci` writes hard-coded its `python-version` matrix
    in jm's template, and the generated `pyproject.toml` declared no version
    classifiers, so a project could not drop a Python without hand-editing
    a workflow `jm ci --force` would put back. `jm new` now writes
    `Programming Language :: Python :: 3.N` classifiers, and
    `requires-python`, from jm's own installed metadata, and the README's
    `Python 3.N+` line from that floor; the GitHub workflow's matrix is the
    classifiers `requires-python` admits, the Woodpecker image the oldest of
    them, and every `jm apply` rewrites that one value to match -- a
    one-line, multi-line or block list, kept in its shape -- so
    `jm status --check` reports the workflow STALE until it does. A
    classifier `requires-python` refuses gets no leg, and is named. A
    workflow whose list jm cannot locate is never written, and is named by
    `apply` and listed by `jm status` under `CI PYTHONS`. The workflow stays
    static YAML. A project with no version classifiers keeps its
    `pyproject.toml` untouched and its CI follows jm's list, with a one-line
    note saying to add them. jm now depends on `packaging` for the
    `requires-python` reading (PEP 440 specifiers).
