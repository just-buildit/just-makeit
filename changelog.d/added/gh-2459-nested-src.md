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
    `SUPERSEDED`. `jm remove` takes only a component's own files when
    another component's directory sits below its own.
- **Where a native file was is jm's record, never read off the tree**
    (gh-2459, gh-2463). `jm apply` now records what it placed --
    `[project] applied_layout` and, under `nested`, each module's
    `applied_package` -- and every layout or package move is computed from
    that record. jm used to infer a former place from CMake files and the
    sacred core's first include, which could take a vendored directory in
    jm's shapes for a component's, or strand a module's headers after a
    `package` change with only a raw symbol collision to show for it. A flat
    project carries no record and its manifest is unchanged; a file jm has
    no record of placing is named by `jm status` (`ADVICE`) and by the
    collision it causes, and never moved.
