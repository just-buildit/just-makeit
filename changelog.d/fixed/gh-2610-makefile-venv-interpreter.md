- **The generated `Makefile` builds against the project's `.venv`, and
    installs into one that has no pip** (gh-2610). `make` took the first
    `python` on `PATH` while the generated `CMakePresets.json` named
    `.venv` (gh-814), so building one project both ways compiled it for two
    interpreters. It now uses `.venv` whenever it exists
    (`.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere),
    in both build
    backends, as `jm build` already did; `PYTHON=` and `JUST_BUILDIT_PYTHON`
    still win, and with no `.venv` it reads `PATH` as before. Its numpy
    repair and pytest install ran `$(PYTHON) -m pip install`, which in a
    venv `uv sync` made ends in `No module named pip`. Both now run pip when
    the interpreter has it, `uv pip install --python <it>` when it does not,
    and otherwise stop naming both commands. `jm status` reports an
    existing project's `Makefile` `OUTDATED` until it takes the new render.
    The generated `README.md` says which interpreter `make` uses, and points
    Visual Studio users at creating the `.venv` first.
