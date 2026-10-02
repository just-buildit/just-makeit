- **An array argument refuses a `str` only when its param declares
    `str_hint`** (gh-1824). gh-1700 made every generated array argument
    refuse a `str`, a policy jm imposed on every project. Now a param
    without the key converts a `str` the way numpy does
    (`np.asarray("0101", dtype=np.uint8)` is the one element 101), and a
    param declaring `str_hint` opts in: it refuses a `str` with the hint
    appended, exactly as before. A `uint8_t[]` / `int8_t[]` param still
    reads `bytes`, `bytearray` or `memoryview` as its bytes. A project
    picks it up when `apply` re-renders the extension glue that carries
    the helper. `apply`'s advisory for a fragment
    predating `jm_array_arg` now names only a `uint8_t[]` / `int8_t[]`
    argument, the one element type where the old bare conversion still
    differs.
