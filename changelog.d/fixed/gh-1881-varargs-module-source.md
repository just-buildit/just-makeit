- **A `--varargs` method on a module object builds under `c_prefix`**
    (gh-1881). The module's `Python3_add_library` named the method's binding
    file after its C symbol, `../g/p_g_configure_core.c`, while the file jm
    writes is `g_configure_core.c`: a file stem stays the object's name. So
    a default `jm new` project, which sets `c_prefix`, failed CMake
    configure as soon as a module object gained a varargs method, and
    `jm status` reported it clean because its replay rendered the same
    path. Every side now names the file through one helper, and the
    `_ext.c` comment that pointed at the missing file names the real one.
    `jm apply` repairs an existing module's `CMakeLists.txt`.
