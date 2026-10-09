- **The replay renders the project's version, not `0.1.0`, so an in-sync copy
    is no longer reported OUTDATED** (gh-2083). `apply` compares every
    create-only copy of `[project] version` against a replay of the scaffold,
    and that replay ran with no version. On a project at any other version,
    `status` named an in-sync `Doxyfile` or `bootstrap.toml` as stale, and
    `apply` wrote a missing copy at `0.1.0`, then warned that it disagreed
    with the manifest. Both now read the manifest's version.
- **A PEP 440 pre-release has a CMake spelling: its release segment** (gh-2084).
    `project(VERSION)` takes integers only, so `1.1.2a47` used to leave
    `CMakeLists.txt` out of step with the manifest, with no value that both
    would accept. The CMake copy now carries `1.1.2`, and the pre-release stays
    the manifest and PyPI version. `jm config version 1.1.2a47` writes it that
    way, and `status --check` is clean. An epoch (`1!2.0`) or more than four
    integer components has no CMake spelling, so that copy is still named as
    unwritable.
- **The VERSION advice names the command that writes every copy** (gh-2102).
    It used to say "Sync whichever is wrong". It now prints
    `jm config version <manifest version>` to keep the manifest, and
    `jm config version <copy version>` for each distinct copy value, to keep
    that copy instead.
