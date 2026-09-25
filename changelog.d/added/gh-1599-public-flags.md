- **`[project] public_link_libs` and `public_defines`** (gh-1599): link
    flags (`-lpthread`) and definitions (`_GNU_SOURCE`) the installed headers
    need of every consumer. Each reaches this project's own compile and
    executables, both combined libraries' public face and so the exported
    targets, and the installed `.pc`'s `Libs:` and `Cflags:`. A CMake target
    (`Threads::Threads`) is refused, pointing at `find_packages`. Removing a
    dependency or flag from the manifest now also clears it from the root
    `CMakeLists.txt`'s external-deps block; it used to linger there once the
    last one was gone.
