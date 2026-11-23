- **An enum-backed method parameter is annotated `Literal[...]` of its
    names in every stub, and the init_param enum spelling on one is refused
    with a sentence** (gh-2736, gh-2737). A method parameter declared
    `type = "int"` plus `enum = "<name>"` read `str` in both `.pyi`
    producers, while an init param's `string_enum:` read `Literal[...]`, so
    a caller deriving values from the stub could not find the legal ones.
    Every enum-backed parameter -- a method's, a view method's, a module
    function's, a handle's, and a standalone object's `string_enum:` init
    param -- now reads `Literal[...]`, on its signature and its docstring's
    type line alike. Separately, `type = "string_enum:a,b"` or
    `type = "enum:<name>"` on a method or function parameter passed
    `apply`'s check and crashed every method shape with a `KeyError` in
    `scalar_arg_c`, after printing the type string as a C type in a header
    warning. `apply` now refuses it before writing anything, naming the
    `{ name = ..., type = "int", enum = "<enum>" }` row that works.
