- **`jm config version` writes the new version into every generated copy
    of it, so `jm status --check` passes right after it** (gh-2069). The
    verb wrote `[project] version` and nothing else. `pyproject.toml`,
    `bootstrap.toml`, the root `CMakeLists.txt`, the `Doxyfile` and
    `<pkg>_version()` in `native/src/<pkg>_lib.c` all kept the old value,
    and `status --check` reported each one. A `pep723` app script was
    STALE too, until the next `jm apply`. `apply` still rewrites none of the
    create-only copies, because from the tree alone it cannot tell which side
    moved. The verb can: it is the author declaring the value. It writes each
    copy `status` checks, through the same table `status` reads, replacing
    only the value. It re-renders the recorded app the way `apply` does. A
    copy the build derives (`PROJECT_NUMBER = $(VAR)`) is left alone. A
    value a copy cannot hold is not written, and the verb names that copy:
    `project(VERSION)` takes integers only, so a pre-release such as
    `1.0rc1` stays out of `CMakeLists.txt` (gh-2084). On a project whose
    manifest omits the version and reads it from `pyproject.toml` (gh-1283),
    the verb used to do nothing. It now writes `pyproject.toml`, and the
    manifest still omits the version. The root `CMakeLists.txt` copy is now
    read from the `project()` command itself, wherever a formatter puts it,
    and never from another command's `VERSION`.
