- **`apply` replays every key its writers accept** (gh-2215, gh-1893). The
    replay dropped a capsule property's `capsule_type`, so a module object
    constructed from that capsule through an `object` init-param made
    `apply` refuse a project `jm property` had built. It now also forwards
    `step_delegates_to_steps`, `opaque_state`, a method's `bench = false`,
    the `[project]` dependency keys and the `[module.X]` `extra_*` keys,
    and a test reads every writer's signature so the next dropped key fails
    it. `jm object --init-param src:object:nco` no longer crashes with
    `KeyError: ''`, a module object includes that producer's header in its
    `_core.h`, and `jm script` learns `--opaque-state` and `--no-bench`. An
    opaque-state object no longer gets the starter `gain` field.
- **`jm script` rebuilds a project whatever order its `object` references
    run in** (gh-2215). The reference is resolved when the consumer is
    created, so the script now creates each producer first, with its
    records, properties, methods and views (a reference may name a view),
    even when the manifest lists the consumer earlier. A module keeps its
    declared order through the new `jm object --module M --before OBJ`,
    which inserts the object ahead of `OBJ` in `[module.M] objects` -- its
    load order -- instead of appending it.
