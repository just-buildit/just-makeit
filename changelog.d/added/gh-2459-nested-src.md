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
- **jm writes, and moves, only what it can show is its own** (gh-2459).
    Before moving out of the place its record names, `jm apply`
    (and `jm upgrade`) replays the recorded manifest and requires the
    component's
    CMakeLists there to be jm's render, byte for byte; a stale or
    hand-edited record is refused, naming both places. A component's new
    place must be empty or hold only what jm renders there, and a sacred
    `_core.c` / `_core.h` already there is always someone else's: a vendored
    directory where a new component would go is refused and left alone,
    whatever its files are named, and `jm object` / `jm module` refuse
    directories that already hold files. This changes the flat layout too: a
    generated module whose directory already holds a hand-written
    `native/src/<module>/CMakeLists.txt` used to have it overwritten and is
    now refused -- declare `no_generate = "true"` to keep the directory
    yours. And a CMakeLists rendered by an older jm fails the check on a
    layout switch or `package` change: restore it, run `jm apply` once on
    this version, then change it. A project last applied by jm 0.119 or
    older should run `jm apply` once before changing a layout or a module's
    `package`; `apply --only` refuses while files wait to move.
- **Under `nested`, jm records the members it placed in each module**
    (gh-2459): `[module.X] applied_objects`, beside `applied_package`,
    written by `jm apply` and by the `jm object` that creates a member. A
    member not on it -- declared since by a fragment -- has never been
    placed by jm, so a directory already at its place, even one holding a
    `<name>_core.c`, is checked and refused rather than written into.
    `jm remove` takes the member off it. A module recorded before the key
    existed has its members shown from the tree (their CMakeLists must be
    jm's render) and recorded on the next `apply`.
