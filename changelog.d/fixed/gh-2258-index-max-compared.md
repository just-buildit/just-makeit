- **A `size_t` argument above `SIZE_MAX` raises `OverflowError` on a
    target where `size_t` is 32 bits** (gh-2258). The checked parse gh-2220
    added converts a `uint64_t` / `size_t` through
    `PyLong_AsUnsignedLongLong`, which refuses only what an
    `unsigned long long` cannot hold, so on such a target a value between
    `SIZE_MAX` and `2**64` passed it and wrapped on the cast with no error.
    The converted value is now compared with the type's maximum before the
    cast and refused in the same words. Nothing changes where `size_t` is
    64 bits: the comparison is always false there, and the compiler drops
    it. The maximum is read from the type's one range declaration, which
    the generated command line and a composer's JSON reader read too.
