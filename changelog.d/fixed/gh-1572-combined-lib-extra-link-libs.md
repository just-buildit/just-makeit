- **The combined `lib<pkg>` libraries now link their cores' `extra_link_libs`**
    (gh-1572). The root folds each core in by its objects alone, and objects
    carry no link requirements, so a core calling into an external package
    left the shared library with those symbols undefined. macOS and Windows
    refused to link it, and Linux linked it, leaving the failure to the first
    C program that used the `.so`. Each component's CMakeLists now links the
    libraries into `lib<pkg>` (PRIVATE) and `lib<pkg>_static` (PUBLIC), the
    same fix doppler applies for `Threads::Threads`. The installed
    `<pkg>-config.cmake` also gains a `find_dependency()` for each
    `[project] find_packages` / `pkg_modules` entry, so a consumer of the
    static library can resolve it. An existing project picks up the link lines
    and the root variable on its next `jm apply`. `jm status` reports its
    `cmake/<pkg>-config.cmake.in` as OUTDATED until that file is refreshed.
