- **Switching `[project] layout` keeps your own CMake targets building**
    (gh-2429). `jm apply` respelled a moved C test's path in the root
    `CMakeLists.txt` and a hand-owned component one, but not in a
    component's `<c>_extra.cmake` hook, so `status --check` was clean and
    cmake configure failed. And a target of yours whose source it respelled
    lost the directory the source had left, so a header beside it
    (`jm_test.h`, or your own) was no longer found. The hook is now
    respelled in both directions, and each such target gets one
    `target_include_directories` line naming the directory its source left,
    as jm's own test target does. Before the move, `jm status` lists each
    pending move once, under `SUPERSEDED`, naming `jm apply` as the command
    and counting it (`N to move`), rather than also as a file `apply` would
    create with advice to run `upgrade`.
