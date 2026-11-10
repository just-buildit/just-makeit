- **A generated benchmark builds warning-clean under clang-cl** (gh-2294).
    `jm_bench.h` called `fopen` and `localtime`. The Windows UCRT
    deprecates both in favour of their `_s` forms, and clang-cl warns about
    them by default, so a bench built with `-Werror` on Windows failed in
    the shared header rather than in anything the author wrote. On Windows
    the header now calls `fopen_s` and `localtime_s`; every other platform
    is unchanged. jm's own compiled tests found this the first time they
    ran under clang-cl.
