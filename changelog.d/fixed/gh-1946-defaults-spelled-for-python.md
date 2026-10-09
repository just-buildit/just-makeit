- **A default is one value on every face, spelled for Python where Python
    reads it** (gh-1946, gh-1947). A state field declared
    `a:double:0.1L` scaffolded a test, a stub and a stub doctest that were
    all SyntaxErrors: the float default kept every suffix but `f`, so `0.1L`
    reached Python verbatim. A hex default for a float field became
    `0x10.0`, an octal `010` stayed `010` (a SyntaxError for an integer, and
    10.0 rather than C's 8.0 for a float), and `0.1f` in a `double` field
    read as `0.1` in Python while C stored 0.10000000149011612. One function
    now gives every Python face (the generated test, the stub's signature,
    Parameters entry and doctest, the runtime docstring, a method or
    function parameter) the Python spelling of the declared value, from one
    reader of C literals. A stub doctest prints what the getter returns: a
    `float _Complex` default of `0.1` showed `(0.1+0j)` where the getter
    returns `(0.10000000149011612+0j)`, so `pytest --doctest-glob='*.pyi'`
    failed on a fresh scaffold. Two literals have no Python spelling and are
    now refused where they are declared, on the command line and in a
    manifest: an octal with an 8 or 9 in it (`08`), and a floating literal
    for an integer type (`1.5` or `1e3` into an `int`), which C converted
    silently and Python refused. An init-param's default refusal is an
    `error:` line rather than a traceback.
