- **An installed library finds the libraries it needs** (gh-1869). jm's
    installed shared libraries carried no RUNPATH, and a program's own rpath
    reaches only its direct dependencies. So from a non-system prefix a
    program that named one package, whose library needs another, did not even
    link (`libalpha.so, needed by libbeta.so, not found`), and a
    `find_package` consumer of it failed to start. On Linux and the BSDs each
    installed library now carries RUNPATH `$ORIGIN`, for the libraries beside
    it, plus the directory of any dependency in another prefix. Both are
    defaults of CMake's own `CMAKE_INSTALL_RPATH` /
    `CMAKE_INSTALL_RPATH_USE_LINK_PATH`, so a packager's setting wins. A
    program now needs only the rpath to what it links, which the rewritten
    "Runtime loading" section of the C library guide gives per platform. The
    `.pc` itself carries no rpath. `jm apply` updates an existing project's
    install section.
