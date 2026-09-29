- **A composer `bit_pattern` field can read a `str` with the project's own
    grammar** (gh-1709). `coerce_str_fn = "<fn>"` on the field names a
    project function with the signature
    `size_t fn(const char *, uint8_t *, size_t, const char **)`, which jm
    declares in the module's `_bridge.h`. jm calls it once with `out = NULL`
    to size the pattern and again to fill it. A return of 0 raises
    `ValueError(why)`. The constructor, the property setter, a segment's
    single-source keywords and the c-face CLI flag all read text this way,
    so a string the project accepts works on every face and one it refuses
    (such as `""` or `"0x"`) is refused everywhere. `bytes` and int
    sequences work as before. The key is set per field.
