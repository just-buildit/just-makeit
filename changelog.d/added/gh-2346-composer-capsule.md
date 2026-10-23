- **A composer module can publish its state as a capsule** (gh-2346).
    `[module.X] capsule = "<name>"` (or the shared `capsule_name`, which a
    composer used to accept and ignore) gives the generated composer type a
    read-only `_capsule` lending its `<sym>_state_t *`, borrowed and holding
    the composer alive, as a handle's (gh-794) and an object's (gh-788) do.
    Another module's method then takes the `Composer` itself through a
    `capsule` param, `retain = true` included; a closed composer is refused.
    One reader, `published_capsule`, serves handles and composers.
- **A lent pointer outlives its owner's `close()`** (gh-2346). `retain`
    keeps the producer object alive, but `close()` freed its pointer anyway,
    so a consumer reading it afterwards read freed memory: a
    heap-use-after-free a handle publishing a capsule has had since gh-794,
    and an object publishing `_capsule` since gh-788. Once lent, a composer's
    or handle's `close()` only marks it closed and dealloc frees the pointer;
    an object parks its state at `destroy()` as one lending views does
    (gh-2187). For a handle, its close function and the status it reports
    wait for the last borrower, and a lent handle refuses a second
    `__init__`.
