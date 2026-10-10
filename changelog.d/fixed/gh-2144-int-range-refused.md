- **An integer outside its C type's range raises `OverflowError` instead
    of wrapping silently into C** (gh-2144). A narrow integer parses into a
    wider local (an `int8_t` into an `int`), and the binding cast it with no
    range test, so `int8_t` given `300` arrived as `44`. This happened on
    every face that takes a scalar: constructors, `set_<field>()`, writable
    properties, method params, `step()`, module functions and composer
    rows. `uint8_t` and `uint16_t` parsed through PyArg's masking `I`, so
    even `2**32 + 5` arrived as `5`. `uint32_t` parsed through `k`, so on
    Windows `-1` arrived as `4294967295`. Every `int8_t`, `int16_t`,
    `int32_t`, `uint8_t`, `uint16_t` and `uint32_t` value is now tested
    against its `<stdint.h>` bounds before the cast. The unsigned ones parse
    through a checked format char. A value outside the bounds raises
    `OverflowError: gain: 300 is out of range for int8_t [-128, 127]`, as
    numpy 2 refuses `np.int8(300)`. The code generated for every other type
    is unchanged. A module object's fragment rendered before this fix has
    no range test, and `jm apply` names each member that lacks one. Still
    open: `uint64_t` and `size_t` parse through the masking `K`, so `-1`
    still arrives as `2**64 - 1` (gh-2220). `kind = "handle"` and
    `kind = "capsule"` modules do not convert through the checked path yet
    (gh-1952).
