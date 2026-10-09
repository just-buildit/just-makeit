- **`jm add --state`, `jm remove state` and `jm regenerate` refuse an object
    in a `no_generate` module, and write nothing, where they deleted its
    hand-written files** (gh-2087). Each rebuilds the object by deleting its
    files and having `jm apply` write them again from the manifest, and
    `apply` writes nothing of a `no_generate` module. So on one of its
    objects the delete was all that happened: `_core.c` with the author's
    edits in it, `_core.h`, the object's CMakeLists, its binding fragment,
    its C test and bench were gone, the command exited 0, and `apply` said
    there was nothing to do. The rebuild now refuses before the first
    delete, and `add` and `remove state` refuse before they ask or save, so
    the tree is left exactly as it was; the message says to edit the C by
    hand.
