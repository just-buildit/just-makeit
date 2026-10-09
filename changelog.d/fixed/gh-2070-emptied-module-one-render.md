- **Removing a module's last object or function leaves the tree `jm apply`
    writes, and an empty module keeps its `<module>_extra.cmake` hook**
    (gh-2070). A module had two renders: the one every member verb and
    `jm remove` use, and a second for a module with nothing in it, which
    `jm module` wrote and `jm apply` replayed an empty module through. So
    after `jm remove object` (or `jm remove function`) took a module's last
    member, `jm status --check` reported its `CMakeLists.txt` and `_ext.c`
    STALE, and `jm apply` rewrote them without the gh-1351 `include()` of
    `<module>_extra.cmake`, warning that it was dropping jm's own line. The
    empty render also ignored `jm module --extra-include-dirs` and
    `--extra-link-libs` until the module's first member arrived. Now a
    module has one render whatever it holds: an empty module's CMakeLists
    carries the hook and its declared include directories and libraries,
    and its `_ext.c` is the same aggregator a module with members gets.
