- **A `uint64_t` or `size_t` argument outside its range raises
    `OverflowError` instead of wrapping into C, and takes a numpy integer**
    (gh-2220). Both parsed through PyArg's `K`, which masks, so `-1`
    arrived as `2**64 - 1` and `2**64` as `0` before any range check could
    see them: a `size_t` count given `-1` became `SIZE_MAX`. `K` also
    refused a numpy integer before Python 3.14, so `np.uint64(5)` raised
    `TypeError`. Both now parse an object and convert it through
    `__index__` and the checked `PyLong_AsUnsignedLongLong`, so a value
    outside the range raises
    `OverflowError: n: -1 is out of range for size_t [0, 18446744073709551615]`,
    in the words every other integer type uses, and any integer with
    `__index__` is accepted. A `float` is refused with `TypeError`, as for
    every integer type. This holds on every face: constructors,
    `set_<field>()`, writable properties, method and module-function params,
    `step()` and its controllable overrides, composer rows, and handle and
    capsule modules. The constructor, `set_<field>()`, `step()`, override
    and `variable_output` parses now go through the one conversion every
    other face uses instead of spelling their own. A parameter named
    `<x>_raw` or `<x>_obj` beside a scalar `x` that the conversion declares
    such a local for is refused when it is declared, where it used to
    produce C that did not compile. A module object's fragment rendered
    before this fix still parses through `K`, and `jm apply` names each
    member whose format no longer matches.
