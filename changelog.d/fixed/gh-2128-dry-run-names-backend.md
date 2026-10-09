- **`jm dry-run` names the build its backend runs** (gh-2128). It always
    printed a cmake configure, with jm's own interpreter as
    `Python3_EXECUTABLE`, whatever `[project] build` said. On a `build = "make"`
    project that described a build the project never runs. A make project now
    shows `make PYTHON=<project python>`, and a cmake project shows its configure
    with the project's `.venv` interpreter when one exists.
