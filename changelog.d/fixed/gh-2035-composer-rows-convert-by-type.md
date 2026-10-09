- **A composer's settings, segment fields, computed properties, source
    fields and serializer params convert a value by its declared type**
    (gh-2035). Every setting crossed through `PyLong_AsLong` /
    `PyLong_FromLong` whatever its `type`, so a `double` setting whose C
    value was 0.75 read back as `0`, and `0.5` was refused on the way in
    (truncated on Python 3.9). The other rows went through a private table
    of five types that sent every other one through `long`: a `bool` read
    back as an `int`, a complex computed property as its real part, a
    complex segment field could not be set, a `size_t` source field refused
    `2**63`, and a serializer param of a type outside the five was parsed as
    an `int` -- so an `int64_t` reached C as 4294967295 for `-1` and an
    `int16_t` as `0`. Each face now converts through the row every object
    face uses (the type's format char, its `parse_type` local, `to_py`), and
    the `.pyi` names each row's Python type. A type no face can convert --
    an array, a spelling jm does not know, a string on a row that holds a
    number, or a complex segment field the JSON or CLI face would carry as
    one real number -- is refused by `jm apply` with one `error:` line
    naming the row, and nothing is written.
