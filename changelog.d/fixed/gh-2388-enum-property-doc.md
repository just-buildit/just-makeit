- **An enum-backed property is documented by its choices, not by the C int's
    comment** (gh-2388). A property bound to an `[[enum]]` returns one of the
    strings (`Literal["le", "be"]`), but with no manifest `doc` and no getter
    `@brief` its text came from the state struct member's trailing comment,
    which describes the `int`: `0 little, 1 big.` beside a return type that
    is not a number. It now reads `One of "le", "be".`, in the runtime getset
    doc and the `.pyi`, from the same sentence as an enum-backed parameter
    (gh-2379). A manifest `doc` and a getter `@brief` still win. The generated
    `get_<name>()` and `set_<name>()` accessors return the raw `int`, so they
    keep the C comment. `jm status --docs` still lists such a property, since
    the choices are generated and not written.
