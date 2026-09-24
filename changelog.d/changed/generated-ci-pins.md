- **The CI `jm ci` generates is pinned the way jm's own is.** The generated
    `.github/workflows/ci.yml` used `actions/checkout@v4` and
    `actions/setup-python@v5`. Both run on Node 20, which GitHub now forces
    onto Node 24 with a deprecation notice on every run and will stop running.
    It now uses `checkout@v7.0.1` and `setup-python@v7.0.0`, and its matrix
    adds Python 3.14. A new test holds the template to jm's own workflow pins
    (which Dependabot never sees in a template) and to jm's supported Python
    range. An existing project keeps its file; `jm ci --force` rewrites it
    from the template (hand edits to it are lost).
