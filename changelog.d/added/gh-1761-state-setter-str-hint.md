- **An array state field's setter can say where text goes** (gh-1761). A
    `str_hint` on a `[[<obj>.state]]` field typed `T[N]` is appended to its
    `set_<name>`'s refusal of a `str`, through gh-1756's one converter:
    `TypeError: sync must be an array of numbers, not str: build bits from text with field_bits()`. A field without the key renders exactly as
    before. The key lives on the state row because that is the setter that
    converts an array: no `[[<obj>.properties]]` setter does, so `load`
    refuses a `str_hint` on a property and names the state row, and refuses
    one on a scalar or `opaque` state field. `jm apply` replays the key, and
    `jm status` reports a sacred fragment whose `set_<name>` predates it.
    The manifest dumper used by `jm split-objects` and
    `jm migrate-to-fragments` now writes every state key, so it no longer
    drops a state field's `doc` (gh-1493) either.
