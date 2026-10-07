- **A capsule or handle row typed as an array is refused, naming the row,
    instead of crashing `jm apply`** (gh-2009). A capsule module's
    `init_params` and a handle module's `create_args` parse one C scalar per
    row, and both looked the type up in jm's scalar table directly, so
    `type = "float[]"` died with a bare `KeyError: 'float[]'` naming neither
    the module nor the row. The same lookup crashed a capsule's
    `properties`, a handle factory's `init_params` and a handle getter's
    `fields` on an array, and every one of those -- plus a handle method's
    scalar `args` and its `returns` -- on a type spelling jm does not know.
    Each now exits 1 with one `error:` line naming the module, the table
    and the row, and leaves the project untouched; on a constructor row it
    adds that a constructor array is supported on an object's
    `[[<obj>.init_params]]`.
