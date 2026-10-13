- **A composer's ranged field crosses the Python face at its own type**
    (gh-2224). A ranged source or segment field -- a scalar or a
    `(lo, hi)` pair -- parsed each bound as a `double` and cast it, and read
    the pair back through `"(dd)"` on a source and `Py_ssize_t` on a
    segment. So a `uint64_t`, `int64_t`, `size_t` or `ptrdiff_t` bound past
    2^53 changed when set and again when read, an integer source pair read
    back as two floats, a `double` segment pair read back truncated to
    integers, and an `int8_t` given 300 wrapped to 44, all with no error.
    Each bound now parses through the field's own `_CTYPE_META` row
    (`scalar_parse_c`, the parse a plain field of that type takes) and the
    pair is built from its `to_py`, on the constructor, the attribute and
    `Segment.sum`. So every value round-trips exactly, a bound the type
    cannot hold is refused with an `OverflowError` naming the field
    (gh-2144), and an integer field's bound is an `int`, as its `.pyi`
    always said: a `float` there is now a `TypeError`, as it already was for
    a plain integer field -- and, until gh-2220, so is a numpy integer for a
    `uint64_t` or `size_t` field on Python before 3.14, as for a plain one.
    `_jm_parse_range` now only splits the value into its bound objects.
