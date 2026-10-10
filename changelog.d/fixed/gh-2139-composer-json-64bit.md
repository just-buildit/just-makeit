- **A composer's JSON face carries a 64-bit integer field exactly**
    (gh-2139). The generated `to_json` wrote every numeric source and segment
    field through a `double` and `from_json` read it back through one, so a
    `uint64_t` such as `0x8000000000000001` -- or an `int64_t`, `size_t` or
    `ptrdiff_t` past 2^53 -- came back a different value, with no error. Such
    a field is now a JSON number while its value is within ±(2^53 - 1), the
    range a double holds exactly (I-JSON, RFC 7493), and a decimal string
    past it, so every small value and every existing record reads as before.
    `from_json` / `from_file` read either form exactly, and refuse with a
    `ValueError` naming the field a number outside that range or with a
    fraction, a string that is not a decimal integer or overflows the type,
    and a sign on an unsigned field. A composer with no 64-bit field
    generates the same code as before; the double reader `_json_num` is now
    defined only where some field calls it, as gh-1863 does for the other
    helpers, so one whose every number is 64-bit builds under `-Werror`.
