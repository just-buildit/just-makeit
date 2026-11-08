- **A writable property's read can fail, with its own exception**
    (gh-2543). `getter_status = true` was refused on a writable property,
    because its `error` / `error_message` already name the setter's refusal
    (gh-2182). A second pair, `getter_error` / `getter_error_message`
    (`jm property --getter-error EXC --getter-error-message TEXT`), now names
    what a failing read raises: the getter is
    `int <stem>_get_<prop>(const state *, T *out)` as on a read-only one,
    the setter is unchanged, and the `.pyi` and `help()` document both
    exceptions. It is required on a writable property under
    `getter_status`, refused on a read-only one (where `error` already names
    the read's), and refused without `getter_status`.
- **A buffer view property's read can fail** (gh-2543). A `buf_field`
    property declaring `getter_status` calls
    `int <stem>_<prop>_status(const state *)` before it builds the view, and
    a non-zero return raises `error` with `error_message`; on zero,
    `valid_field` still decides `None` exactly as before. jm declares and
    stubs the status function, through `jm property`, `jm apply` and
    `jm script`, standalone and in a module. `jm property --buf-field` no
    longer tells the author to implement a getter the view never calls.
    `getter_status` on a container property stays refused.
- **A property accessor and a method can no longer derive one C function**
    (gh-2543). A property `eta` and a method `get_eta`, or a buffer view
    `last` with `getter_status` and a method `last_status`, both named one
    C function: whichever was added second rewrote the other's prototype,
    exited 0, and the build failed on "conflicting types". `jm property`,
    `jm method` and `jm apply` now refuse it, whichever comes second,
    naming both members; a method's `--fn` gives it another C name. A
    view's methods and properties are asked too, since a view binds its
    object's core: `jm method --view` adding `get_eta` beside a property
    `eta` is refused the same way.
- **A writable buffer view is refused** (gh-2543). `buf_field` with
    `writable` rendered a setter that did not compile (a scalar parse into
    `T[] v`, and a call to a setter jm never declared). The view's array
    is already writeable in place, so `jm property` and `jm apply` refuse
    `writable` there.
- **A buffer view with `valid_field` is annotated `NDArray[...] | None`**
    (gh-2543). Its read returns `None` while the flag is clear, and the
    `.pyi` said `NDArray[...]` alone, so a type checker accepted code that
    fails on the `None`. A view without `valid_field` is unchanged.
