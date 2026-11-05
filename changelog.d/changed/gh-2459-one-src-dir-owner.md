- **Every C source directory jm writes or reads is asked of one function**
    (gh-2459, part 1 of 2). A component's `native/src/<name>/` was spelled
    by hand at about a hundred places across 37 modules, the root wiring's
    `add_subdirectory(native/src/<name>)` at four more, and the `make`
    backend's rule template at three. They now all go through
    `_incpath.src_dir` / `src_path` / `src_rel`, the root wiring through
    `subdir_line` / `SUBDIR_RE`, and the template through a `<<src_dir>>`
    slot. A test refuses a `native/src/<name>` spelled anywhere else. Nothing
    a project gets changes: whole scaffolds and every bundled example, in
    every layout, are byte-identical to the release before. Part 2 makes
    the C follow a nested module.
