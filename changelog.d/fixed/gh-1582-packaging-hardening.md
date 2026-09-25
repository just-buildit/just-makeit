- **The installed library can be moved, staged and versioned** (gh-1582).
    The `.pc`'s `prefix` is now relative to the file's own location
    (`${pcfiledir}`), so `cmake --install --prefix`, `DESTDIR` staging and a
    moved tree all keep working. Before, the `.pc` named the configured
    prefix, where nothing had been installed. A `CMAKE_INSTALL_LIBDIR` or
    `INCLUDEDIR` given as an absolute path (Nix, Guix) is written as that
    path instead of `${exec_prefix}//abs/lib`. Under a system prefix
    (`JM_PC_SYSTEM_PREFIXES`, default `/usr`) the `.pc` stays absolute, so
    pkg-config can still drop `-I/usr/include`. An explicit
    `-DJM_PC_RELOCATABLE` overrides either way. An empty optional field
    (`URL:`) is left out, and the `.pc` no longer ends in blank lines. The shared library
    gets `VERSION`/`SOVERSION` (`lib<pkg>.so.0.1` under 0.x, `.so.1` from
    1.0), and `find_package`'s version file uses `SameMinorVersion` under
    0.x, where it used to accept a 0.2 for a 0.1 request. The build tree
    exports its targets, so `find_package` works against a build directory
    with nothing installed. The `.pc` takes `Description:` and `URL:` from
    `project(DESCRIPTION / HOMEPAGE_URL)`. An existing project gets new `ROOT CMAKE`
    findings from `status` (`soversion`, `version-compat`,
    `build-tree-export`, `pc-paths`, `pc-tidy`, and `pc-system-prefix` once
    it has the relocatable paths), and its `cmake/<pkg>.pc.in` is
    reported OUTDATED until both are refreshed together. The new
    `.pc.in` reads variables only the new root sets. A new consumer-matrix
    test (`tests/test_gh1584_consumer_matrix.py`) builds and runs a consumer
    through every route, layout and linkage.
