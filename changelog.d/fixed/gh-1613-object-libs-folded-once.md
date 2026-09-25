- **A core's `extra_link_libs` naming the project's own object code no longer
    doubles it in `lib<pkg>`** (gh-1613). Since gh-1572, each entry was
    restated on both combined libraries, and only a bare `<x>_core` was
    recognised as the project's own. So a `$<TARGET_OBJECTS:x>` entry, or a
    bare OBJECT library not named `_core`, landed twice in a library the root
    already folds it into: `multiple definition of ...` at link, or, for the
    bare name on the archive, a CMake generate error (a target in no export
    set). Both are now recognised, and the OBJECT libraries are read from the
    real tree even while `apply` replays.
