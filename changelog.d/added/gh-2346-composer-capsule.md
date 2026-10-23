- **A composer module can publish its state as a capsule** (gh-2346).
    `[module.X] capsule = "<name>"` (or the shared `capsule_name`, which a
    composer used to accept and ignore) gives the generated composer type a
    read-only `_capsule` lending its `<sym>_state_t *`, borrowed and holding
    the composer alive, as a handle's (gh-794) and an object's (gh-788) do.
    Another module's method then takes the `Composer` itself through a
    `capsule` param, `retain = true` included; a closed composer is refused.
    One reader, `published_capsule`, serves handles and composers.
