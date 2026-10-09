- **`jm status` and `jm apply` check and write a project that declares no
    component yet, and `jm new --c-dep` / `--find-package` /
    `--pkg-module` write their root wiring themselves** (gh-2076, gh-2062).
    On a manifest with no object or module, `status` printed "nothing to
    status" and exited 0, and `apply` refused with "nothing to
    materialize". The project's own files were never looked at: a
    `[project] version` the copies disagreed with read clean, and so did a
    `jm new --c-dep vend` project whose root `CMakeLists.txt` had no
    `add_subdirectory(native/src/vend)` -- which only `apply` wrote, so the
    first `jm object` or `jm module` left the file STALE. The same held for
    the `# ── External deps` block a `--find-package` or `--pkg-module`
    declares. Both commands now do their project-level work whatever the
    component count: `status` reports the VERSION drift and the STALE
    wiring, and `apply` writes the wiring. `jm new` writes the
    `add_subdirectory` and the external-deps block when it creates the
    project, through the same writers, so a new project is in sync on its
    merits rather than because nothing was checked.
