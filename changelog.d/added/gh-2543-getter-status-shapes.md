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
