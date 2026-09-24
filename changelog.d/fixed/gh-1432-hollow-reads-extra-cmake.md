- **A test or benchmark wired in `<dir>_extra.cmake` is no longer reported
    `UNBUILT`** (gh-1432). The gh-806 scan read every `CMakeLists.txt` and the
    root `Makefile` / `local.mk`, but not the `<dir>_extra.cmake` hook each
    generated CMakeLists includes, which is where jm tells you to put hand
    CMake. So a hand-written `bench_*_core.c` or `test_*_core.c` built there
    was called "compiled by no build file" and `jm status --check` exited 1
    on correct work; `jm bench` could not discover it either. The hook is now
    read like any other build file, including a `file(GLOB` in it standing
    the scan down.
