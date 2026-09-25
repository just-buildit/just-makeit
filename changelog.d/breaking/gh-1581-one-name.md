- **One library, one name** (gh-1581). The installed `.pc` is named for the
    library -- `libmy_proj` ships `my_proj.pc` (was `my-proj.pc`), as the
    pkg-config guide says: the file name IS the package name -- and the
    exported CMake targets are `my_proj::my_proj` and
    `my_proj::my_proj-static` (were `my_proj::my_proj_lib` and
    `..._lib_static`), as cmake-packages(7) shows them. Consumers of a
    project whose name contains `_` must update `pkg-config` and
    `target_link_libraries` lines; a name without `_` keeps its `.pc`.
    `jm upgrade` renames an existing `cmake/my-proj.pc.in` to
    `cmake/my_proj.pc.in`, keeping its edits and jm's ownership of it.
