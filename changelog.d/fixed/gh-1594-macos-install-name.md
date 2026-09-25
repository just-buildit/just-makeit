- **macOS: a program linked by pkg-config can load an installed jm library**
    (gh-1594). The installed `.dylib` was named `@rpath/lib<pkg>.dylib`, CMake's
    default, so any program without an `LC_RPATH` -- everything but a
    `find_package` consumer -- failed at launch with `Library not loaded`. The
    library now names itself absolutely, at the prefix the install step used
    (`cmake --install --prefix` included; CMake 3.16 names the configured
    prefix). `status` reports an existing root `CMakeLists.txt` that lacks it
    (`ROOT CMAKE install-name`).
