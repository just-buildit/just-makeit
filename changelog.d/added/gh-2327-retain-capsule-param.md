- **A capsule method param can say the kernel keeps its pointer**
    (gh-2327). `retain = true` on a param with `capsule` makes the object
    hold a reference to what it was passed, so a kernel that stores the
    pointer (`set_telemetry`) no longer writes into an object Python has
    collected. A successful call replaces what is held, `None` clears it,
    a call the kernel refuses (its `status_return`, `error_negative`,
    `error_sentinel`, `error_on_empty` or a NULL `borrow`) keeps what was
    held, and dealloc releases it after the C state is destroyed. Every
    method shape jm writes honours it. It is refused on a param with no
    `capsule`, on a `varargs` / `manual_stub` method and on a method a
    composer source delegates. A module fragment rendered before the key is
    named by `apply` and `status`.
