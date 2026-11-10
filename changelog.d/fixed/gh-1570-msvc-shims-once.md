- **Two jm libraries' headers compile together on Windows** (gh-1570). On
    the MSVC ABI `clib_common.h` defines `static inline` complex shims
    (`jm_cabsf` and the rest) under each package's own include guard, so a
    consumer including two installed jm packages defined every shim twice
    and failed to compile. Each shim is now guarded by itself
    (`JM_SHIM_<NAME>`), with the remap that routes the C99 name to it, so
    any mix of packages -- built by different jm versions too -- defines
    each shim exactly once. Found by running gh-1583's side-by-side consumer
    test on the clang-cl leg, which now runs every project-environment test.
