- **A complex default on a method or function parameter compiles**
    (gh-1887). The binding parses a complex parameter into a `Py_complex`
    local, a struct, and seeds it with the declared default so an omitted
    keyword reads that value. gh-1561 gave the seed one spelling for
    constructor parameters, and three copies of the old one survived: method
    parameters, `variable_output` method parameters and module function
    parameters each emitted the default verbatim,
    `Py_complex z_raw = 1.0f;`. `jm apply` exited 0 and the C compiler then
    failed with "invalid initializer". All three now seed the struct as
    `{re, im}`, and an omitted keyword returns the declared value.
    `jm apply` rewrites a standalone object's `_ext.c` and a module's
    function bindings. A module object's binding fragment belongs to the
    author, so `jm status` lists one written before this fix as
    UNEXPLAINED. Deleting it and running `jm apply` regenerates it.
