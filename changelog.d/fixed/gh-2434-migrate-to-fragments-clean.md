- **`jm migrate-to-fragments` leaves a project `apply` would not change**
    (gh-2434). Dry-run on doppler, it found three bugs. `--help` ran the
    migration: five commands never read their arguments (`app` and `perf`
    scaffolded on any of them). Every command but a build one now answers
    `--help` / `-h` with its usage and runs nothing, and one that takes no
    argument refuses one with exit 2. `[[enum]]` and `[codec]` were filed
    as `objects/enum.toml` and `objects/codec.toml`, and the next command
    moved them back. Every reserved section now stays in the manifest. And
    `status --check` then reported two files stale. The root
    `CMakeLists.txt` wiring for a core several components share followed
    the old order, and a package's `__init__.py` lost the names another
    module's `reexports` put in `__all__` once a package sibling was merged
    after it (doppler's `wfm`, 23 names). Both now come out as `apply`
    writes them, whatever the order.
