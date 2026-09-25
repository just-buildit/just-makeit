- **The root `CMakeLists.txt`'s install section is jm's managed block**
    (gh-1589, part 2). It runs from `# ── Install` to a new `# ── End install`
    line, and `apply` renders it -- so the soname, the install-time `.pc`
    prefix, the macOS install name, the build-tree export and every later
    packaging fix reach an existing project, not only a new one. `apply`
    compares the block by CMake command, so your formatter's layout is never
    rewritten, and it never writes outside the block. The seven `ROOT CMAKE`
    rows for fixes inside it become one, `install-block`. `jm adopt   --packaging` hands an older project's section to jm, refusing (and
    naming) any command no released jm rendered there; jm now records every
    command and template line it ever shipped (`make install-history-update`),
    so an older project's own templates adopt without `--accept`.
