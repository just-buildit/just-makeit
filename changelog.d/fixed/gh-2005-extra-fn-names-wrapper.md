- **`jm apply` refuses an `extra_methods` row whose `fn` names something the
    generated binding already declares** (gh-2005). `fn = "Solo_reset"` on
    object `solo` applied cleanly, and the build then failed in the compiler
    with `conflicting types for 'Solo_reset'`: the row's prototype takes
    `PyObject *self`, jm's own wrapper `SoloObject *self`. The names are
    read off jm's render of the binding, not a list -- every wrapper, a
    type's `_dealloc` / `_init`, its `PyTypeObject` and tables, the
    module's `PyInit_` -- and for a module object that is the whole
    translation unit, every fragment the aggregator includes. A composer's
    `[[module.X.extra_methods]]` gets the same refusal (its seam header
    included), and with it the first checks its rows ever had: a row with
    no `name` or `fn`, an `fn` that is not a C identifier and one `fn`
    given two `flags` are refused instead of crashing with a `KeyError` or
    reaching the compiler. Each exits 1 with one `error:` line naming the
    row, and leaves the project untouched.
