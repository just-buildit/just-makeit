- **A PEP 723 app script declares its project's `requires-python`**
    (gh-2631). The three `jm app --target pep723` templates carried a
    literal `requires-python = ">=3.9"`, a second copy of the floor that
    did not follow the project's `pyproject.toml`. The script now takes the
    project's `[project] requires-python`, and every `jm apply` rewrites it
    when that changes; a `pyproject.toml` without one gives jm's own floor.
    No template jm ships names a Python version any more.
