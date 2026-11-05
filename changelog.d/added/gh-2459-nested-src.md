- **Under `[project] layout = "nested"` a component's C follows its module
    too** (gh-2459). gh-2427 put a module object's header, test and bench at
    `<module package>/<object>/`, but its `_core.c`, binding and CMakeLists
    stayed in `native/src/<object>/`, so the C tree still read two ways. Now
    the source directory is `native/src/<module package>/<object>/` (a
    module's own core, functions and binding at
    `native/src/<module package>/<cname>/`). The root `CMakeLists.txt` wires
    that path, and the CMake targets keep their names. A standalone
    component, a `c_deps` directory and a `no_generate` module's
    hand-written one stay where they are. Switching layout, or changing a
    module's `package`, moves the C file by file, your own files beside the
    core included, and `jm status` names each pending move under
    `SUPERSEDED`. Where the C was is read from the line jm wrote for it in
    the root `CMakeLists.txt`, never matched in the tree, so a vendored
    `native/src/third_party/` in jm's own shapes is never moved or rewired.
    `jm remove` takes only a component's own files when another component's
    directory sits below its own. The flat and mirrored layouts write
    exactly what they did before.
