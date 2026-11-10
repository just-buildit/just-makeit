- **Two jm libraries' headers compile together on Windows** (gh-1570). On
    the MSVC ABI `clib_common.h` defines `static inline` complex shims
    (`jm_cabsf` and the rest) under each package's own include guard, so a
    consumer including two installed jm packages defined every shim twice
    and failed to compile. The shims now sit under one guard shared by every
    package. Found by running gh-1583's side-by-side consumer test on the
    clang-cl leg, which now runs every project-environment test.
