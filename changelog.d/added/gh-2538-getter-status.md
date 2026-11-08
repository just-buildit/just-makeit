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
