- **`jm script` replays a handle, capsule or composer module whole
    (gh-2711).** No verb declares a `kind` module, and the script emitted a
    plain `jm module <name>` for one, so `kind`, `backing` and every
    sub-table (`source`, `segment`, `methods`, `create_args`, ...) were
    dropped without a NOTE: the replay held an ordinary empty module where
    the composer had been. The script now re-declares the module's table
    through the manifest's writer, as it does `[[enum]]`, where the module
    stood (a component's `object` init-param may name it), and ends with
    `just-makeit apply` to write its binding.
