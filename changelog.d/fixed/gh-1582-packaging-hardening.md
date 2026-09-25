- **The installed `.pc` names where it was actually installed, and the
    library is versioned** (gh-1582). The `.pc`'s `prefix` is now written
    at INSTALL time, absolutely. `cmake --install --prefix B` used to leave
    a `.pc` naming the configured prefix, where nothing was installed. A
    `DESTDIR`-staged tree names its real target, which is what a distribution
    package needs. Relocation is the consumer's side of pkg-config:
    `PKG_CONFIG_SYSROOT_DIR` reads a staged tree, and
    `pkg-config --define-prefix` reads a moved one (for `lib/pkgconfig`).
    Under `/usr`, pkg-config still drops `-I/usr/include`. A
    `CMAKE_INSTALL_LIBDIR` or `INCLUDEDIR` given as an absolute path (Nix,
    Guix) is written as that path, where it used to be
    `${exec_prefix}//abs/lib`. An empty optional field (`URL:`) is left out,
    and the `.pc` no longer ends in blank lines. The shared library gets
    `VERSION`/`SOVERSION` (`lib<pkg>.so.0.1` under 0.x, `.so.1` from 1.0),
    and `find_package`'s version file is `SameMinorVersion` under 0.x, where
    it used to accept a 0.2 for a 0.1 request. The build tree exports its
    targets, so `find_package` works against a build directory with nothing
    installed. The `.pc`'s `Description:` and `URL:` come from
    `project(DESCRIPTION / HOMEPAGE_URL)`. An existing project gets new
    `ROOT CMAKE` findings from `status` (`soversion`, `version-compat`,
    `build-tree-export`, `pc-paths`, `pc-tidy`), and its
    `cmake/<pkg>.pc.in` is reported OUTDATED; the new `.pc.in` reads
    variables only the new root sets, so take both together. A new
    consumer-matrix test (`tests/test_gh1584_consumer_matrix.py`) builds and
    runs a consumer through every route, layout and linkage.
