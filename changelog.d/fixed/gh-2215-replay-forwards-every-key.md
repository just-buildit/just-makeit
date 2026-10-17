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
    declared order through `jm object --before`.
- **A cycle of `object` init-param references is refused** (gh-2215).
    Every command that loads the manifest now refuses one, `jm status`
    included, naming it in order (`a -> b -> a`); views' init-params count
    too. A consumer holds a strong reference to its producer, and jm's
    types take no part in Python's cyclic garbage collector, so objects in
    such a cycle were never freed: every one leaked. Break the cycle by
    attaching one side after construction, through a method param declared
    with `capsule` and `retain = true`.
