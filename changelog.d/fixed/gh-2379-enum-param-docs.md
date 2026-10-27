- **An enum-backed parameter's docstring lists its choices, not the C int**
    (gh-2379). A parameter declared `type = "int"` with `enum = "<name>"`
    takes one of the `[[enum]]` strings, but its generated docstring repeated
    the header's `@param` text, which describes the C `int`: `endian : str` /
    `0 little, 1 big.` beside a signature that refuses a number. It now lists
    the choices (`One of "le", "be".`) on every face that documents a parameter:
    an object's method (standalone and in a module), a module function, a
    handle's method, and an object's or handle's constructor, in the runtime
    `__doc__` and the `.pyi` alike. A manifest `doc` on the parameter is still
    shown, ahead of the choices. A `string_enum` init parameter said the same
    thing three ways (`One of: le, be.` in the standalone `.pyi` template, the
    header's prose in the stub); it is now the one sentence. Prose a header
    author wants kept for such a parameter goes in its manifest `doc`.
