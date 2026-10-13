- **A composer's JSON reader refuses a value its field's C type cannot
    hold** (gh-2236). The exact reader #2221 added for a 64-bit field
    (gh-2139) parses into an `unsigned long long` / `long long` and then
    cast that to the field's type. A `size_t` or `ptrdiff_t` parses through
    a `long long` on every target and is 32 bits on a 32-bit one, so there
    `"4294967296"` -- or the number 4294967296 -- passed the reader and the
    cast dropped its high bits, with no error. The value is now converted
    back and compared before it is stored, and one the cast would change is
    refused with the reader's own `ValueError`, naming the field and saying
    it is out of range. Nothing changes on a 64-bit target, where the cast
    loses nothing.
