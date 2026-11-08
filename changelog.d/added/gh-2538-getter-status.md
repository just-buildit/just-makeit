- **A read-only property's getter can fail, and the read raises** (gh-2538).
    `getter_status = true` (`jm property --getter-status`) makes the C
    getter `int <stem>_get_<prop>(const state *, T *out)`: 0 means it wrote
    the value, and a non-zero return raises the property's `error` (default
    `ValueError`) with `error_message` and the return code. Before, a getter
    had no failure path, so a member that does not apply to the object's
    configuration had to read as `NaN` or `0`, which looks like a
    measurement. `AttributeError` joins jm's error categories, the natural
    exception for "this property does not apply here": `hasattr` reads it as
    absence. jm declares and stubs the getter in that shape, the `.pyi` and
    `help()` document the exception in a `Raises` section, it composes with
    `enum`, and `jm apply`, `jm script` and `jm bind` carry it. `error` on a
    read-only property is still refused without `getter_status`, and
    `getter_status` is refused, naming the fix, wherever there is no C getter
    call to test: a writable property, `field`, `expr`, `buf_field`,
    `capsule`, a container or `json` property, and one named for a state
    field. A property without it renders exactly as before.
- **A module object's wrapper that ignores a declared status is refused,
    not kept silently** (gh-2538). A sacred fragment keeps every wrapper
    body it finds, so a property moved from `expr`, `field` or a plain
    getter to `getter_status` (or a setter gaining `error`) went on doing
    the old thing at runtime -- never raising -- while the header, stub and
    `.pyi` said otherwise and `status --check` passed. `jm apply` now
    refuses such a wrapper, naming it and the fix: delete the wrapper and
    its `PyGetSetDef` row and re-run `jm apply`, which renders both fresh.
    jm still never rewrites a wrapper it finds, since it cannot tell its own
    old render from an author's edit. `apply` also refuses while the header
    declares the getter in the other shape from the one its binding calls,
    naming the prototype to write, and `status --check` fails on both
    (`PROPERTY STATUS`).
