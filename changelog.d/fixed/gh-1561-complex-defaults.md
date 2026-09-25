- **A complex default means one value on every face** (gh-1561). `jm object   zz --init-param "z:double _Complex"` no longer exits 1: jm refused its own
    zero, `0.0 + 0.0 * I`, as a default. A complex default is now accepted as
    `re`, `im * I` or `re + im * I`, and every face reads the same value.
    Before, the `.pyi`, the docstring, the generated test and the generated
    app all said `0j` whatever was declared (the app wrote `0.0 + 0.0 * I`,
    a NameError). The binding's `Py_complex` seed is `{re, im}`: an init-param
    default no longer emits C that does not compile, and a state default
    feeding the constructor no longer reads 0 until `reset()`, which made a
    fresh scaffold's own `test_reset` fail. The stub generator's Python-type
    table is now derived from `scalar_py_annotation`, which it had drifted
    from (`ptrdiff_t` and `long double _Complex` were missing).
