- **`apply` replays every key its writers accept** (gh-2215, gh-1893). The
    replay dropped a capsule property's `capsule_type`, so a module object
    constructed from that capsule through an `object` init-param made
    `apply` refuse a project `jm property` had built. It now also forwards
    `step_delegates_to_steps`, `opaque_state`, a method's `bench = false`,
    the `[project]` dependency keys and the `[module.X]` `extra_*` keys,
    and a test reads every writer's signature so the next dropped key fails
    it. `jm object --init-param src:object:nco` no longer crashes with
    `KeyError: ''`, a module object includes that producer's header in its
    `_core.h`, and `jm script` emits a producer's capsule before the object
    that names it, plus `--opaque-state` and `--no-bench`. An opaque-state
    object no longer gets the starter `gain` field.
