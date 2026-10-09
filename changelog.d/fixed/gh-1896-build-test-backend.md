- **`jm build` and `jm test` drive the project's declared backend, and run
    its packaging and Python tests under the project's interpreter** (gh-1896).
    On a `[project] build = "make"` project, both configured CMake against a
    directory with no `CMakeLists.txt` and exited 1. They now run the
    Makefile's default target (`jm build`) and `make test` (`jm test`), with
    `PYTHON=` pointed at the project's `.venv` when there is one. Packaging
    calls `just_buildit.build_wheel` in that same interpreter, so an
    installed `uv tool` jm, whose environment has neither numpy nor
    just-buildit, no longer packages the project. `jm test` on a make project
    refuses pytest arguments, because `make test` takes none.
- **`jm test` passes a project of only module functions** (gh-1950). pytest
    exits 5 when it collects nothing, and `jm test` read that as a failure.
    `run_generated_pytest` already read it as a pass; both now share that
    verdict.
- **`jm build` and `jm test` carry no fixed time budget** (gh-1832). The
    configure, build, ctest and pytest calls each had a hard-coded 600 s
    timeout, so a cold build on a slow board (603 s measured) failed on the
    budget rather than the build. They are now untimed, as the `jm-run-tests`
    precedent is.
