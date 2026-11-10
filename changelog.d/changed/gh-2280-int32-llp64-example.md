- **The `accumulator` example declares an `int32_t` param, so the Windows
    examples job runs an `int32_t` parse at its range edges** (gh-2280).
    gh-2269 moved `int32_t` arguments from a parse through C `long` to one
    through `long long`, so the refusal reads "out of range for int32_t
    [lo, hi]" whatever the platform's `long` is. Only a test of the type
    table held that, and no bundled example passed an out-of-range
    `int32_t`, so no LLP64 (Windows, 32-bit `long`) build ever ran it.
    `AccCf64.add_int(k)` now takes `--param "k:int32_t"`. The example's test
    reads back `INT32_MIN`, `INT32_MAX` and in-range values exactly, and
    requires `2**31` and `-2**31 - 1` to raise the range-check message, on
    every leg that builds the examples, the Windows clang-cl job included.
    The README teaches the range check, and the method's header doctest
    shows it.
