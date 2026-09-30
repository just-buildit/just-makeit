- **A byte-array output buffer is stubbed as the ndarray it must be**
    (gh-1733). gh-1700 widened every `uint8_t[]` / `int8_t[]` parameter's
    stub to `NDArray[...] | bytes | bytearray | memoryview`, including an
    `out` or `mutable` one -- the caller's buffer for C to fill, which the
    binding refuses unless it is a writable ndarray of the exact dtype. A
    type checker therefore approved `int_to_bin(5, 8, bytearray(8), 0)`,
    which raises `TypeError`, and a `bytes` output can never be written at
    all. The widening now applies to array inputs only, in both stub
    generators and the runtime `__doc__`, for object methods, module object
    methods and module functions alike; an output buffer reads
    `NDArray[np.uint8]`.
