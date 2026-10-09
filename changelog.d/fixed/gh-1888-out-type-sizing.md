- **A function's `out_type` output is sized from a length its call carries,
    or the function is refused: it no longer gets one element** (gh-1888).
    `jm function ramp --param n:int --out-type float` generated
    `void ramp(float *out, int n)` and a binding that allocated ONE float.
    `jm --help` and `docs/types.md` promised "the first integer scalar
    param", which this face never read, so a body filling the `n` it was
    called with wrote past the buffer, a heap overflow AddressSanitizer
    reports at the second write. A variable-output function with no
    `out_size` and no array param, `str` included, did the same. One rule,
    `_outbuf.length`, now sizes every output whose C body is handed a bare
    pointer and never its length: `out_size`, else the integer param an
    `out_type = "T[n]"` names, else the first array param's length. A
    function with none of them is refused before anything is written, by
    `jm function`, `jm apply` and `jm status` alike, and so is a `[n]`
    naming no integer param. `--out-type 'float[n]'` is now accepted on the
    command line, where only the manifest could spell it, and a
    variable output honours its `[n]`, which it ignored. On a method, a
    fixed `out_type` over an array `--arg-type` allocated nothing, because
    the sizing skipped the input `x`. It now reads that input's length,
    ahead of gh-65's first integer param, as `--help` says (`in_len`). A
    method with neither still allocates nothing: refusing it stops `apply`
    on projects that declare one, and that call is left open in gh-2109.
