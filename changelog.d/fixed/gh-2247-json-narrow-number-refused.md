- **A composer's JSON reader refuses a number its integer field cannot
    hold** (gh-2247). `from_json` / `from_file` read every field a double
    holds through `_json_num` and cast it, so an integer field took `1.5`
    as `1`, and a value past its type -- `4294967296`, `-1` or `1e30` into
    a `uint32_t` -- arrived wrapped (`0`, `4294967295`, `0`), a cast that
    is undefined behaviour in C. A `bytes` field's elements were cast into a
    byte the same way, so `256` arrived as `0`. Nothing raised. Every
    integer field narrower than 64 bits, plain or ranged, and every `bytes`
    element now reads only a whole number within its type's range
    (`_types.int_limits`, from the type row) and refuses anything else with
    the reader's `ValueError`, naming the field and saying the value is out
    of range. The factory keeps that exception for every composer, not only
    one with a 64-bit field.
