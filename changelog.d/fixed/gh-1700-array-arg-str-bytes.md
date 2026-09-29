- **An array argument is never text, and a byte array takes `bytes`**
    (gh-1700). Every generated binding converted an array argument with a
    bare `PyArray_FROM_OTF`, which reads a `str` or `bytes` as one text
    scalar and casts it: `Fld("0101")` reached a `uint8_t[]` as the single
    element 101 with no error, and `Fld(b"\x01\x00")` was refused with
    `invalid literal for int()`. All of them now go through one converter,
    `jm_array_arg`, emitted into every extension (objects, modules,
    functions, handle, capsule and composer kinds): a `str` is a `TypeError`
    naming the parameter for every element type, and a `uint8_t[]` /
    `int8_t[]` parameter reads any one-byte buffer (`bytes`, `bytearray`,
    `memoryview`) as its elements, one per byte. `bytes` into a wider array
    is refused rather than parsed as a number. Both stub generators say
    `| bytes | bytearray | memoryview` for a byte-array parameter, and a stub
    annotating an array init-param `npt.ArrayLike` now imports `npt`.
