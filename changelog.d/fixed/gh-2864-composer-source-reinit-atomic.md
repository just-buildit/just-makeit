- **A refused composer source `__init__` leaves the source as it was
    (gh-2864).** The source `tp_init` assigned each field straight into the
    live struct, so a re-`__init__` refused part-way (a `check_fn`
    sentence, an enum name, a ranged pair, a bytes value or an owned
    pointer's parser) left the fields before the refusal written and freed
    the buffers it had replaced. It now converts every field into a copy,
    checks it, and only then waits for a running `steps()`, drops the
    generator, frees the old buffers and owned pointers and commits; a
    refusal frees what it converted. A re-`__init__` that omits a ranged
    field also clears that field's range, rather than keeping the pair a
    previous call set.
