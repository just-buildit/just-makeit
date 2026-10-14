- **An `int32_t` argument out of range says the same thing on every
    platform** (gh-2269). It parsed through `l`, a C `long`, which is 32
    bits on Windows, so there a value past `int32_t` was refused by
    CPython's "Python int too large to convert to C long" before jm's range
    check could say `out of range for int32_t [lo, hi]`. It now parses
    through `L` into a `long long`, as `uint32_t` already did, and every
    value past `int32_t` but inside a `long long` gets jm's message
    everywhere.
