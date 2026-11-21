- **A parameter defaulting to a header constant is named in its docstring
    (gh-2679, gh-2703).** On a method or module function, a `default`
    naming a constant (`LIMIT_NONE`) already seeded the binding and put
    `...` in the stub signature, but no docstring said what that was:
    neither the `.pyi` `Parameters` entry nor the runtime `__doc__` named
    any parameter's default. Both now end the type line with
    `default LIMIT_NONE` (a literal in its Python spelling). A constructor
    parameter declared with `default_raw` is named the same way in the
    class docstring, on the stub and on the runtime `__doc__`, for an
    object, a module object and a view. `jm method` and `jm function`
    accept a `=default` on a `uint32_t`, `size_t`, `int8_t`, complex or any
    other scalar the binding parses through an intermediate, so `jm script`
    replays the default it prints; and `default_raw` on a method or function
    parameter now warns to write the constant as `default`.
