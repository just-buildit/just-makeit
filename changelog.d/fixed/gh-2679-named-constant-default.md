- **A method or function parameter defaulting to a header constant stays
    optional and is named in its docstring (gh-2679).** A `default` naming a
    constant (`LIMIT_NONE`) already seeded the binding and put `...` in the
    stub signature, but no docstring said what that was: neither the `.pyi`
    `Parameters` entry nor the runtime `__doc__` named any parameter's
    default. Both now read `limit : int, default LIMIT_NONE` (a literal in
    its Python spelling). `jm method` and `jm function` accept a `=default`
    on a `uint32_t`, `size_t`, `int8_t`, complex or any other scalar the
    binding parses through an intermediate, so `jm script` replays the
    default it prints; and `default_raw` on a parameter now warns to write
    the constant as `default`.
