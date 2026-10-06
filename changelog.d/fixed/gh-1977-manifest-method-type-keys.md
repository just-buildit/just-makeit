- **`jm apply` and `jm status` refuse a method's unusable `out_type`,
    `multi_output` or `extra_args` type instead of crashing** (gh-1977).
    The manifest's type check covered `arg_type`, `return_type`, `params`
    and `result_fields`, and nothing else, so a type `jm method` refuses on
    the command line (`multi_output = ["void"]`, `out_type = "void"` on a
    plain method, any unregistered spelling) reached the binding and both
    commands died with a bare `KeyError`. A module function's `out_type`
    crashed the same way. Each now exits 1 with one `error:` line naming the
    method or function and the key, and leaves the project untouched.
    `--out-type` and `--multi-output` on `jm method`, `jm object` and
    `jm function` ask the same predicate the manifest does, so the two
    faces refuse the same spellings. That narrows what a hand-written
    manifest may say: a method's `out_type` must name an array element
    (`float`, not `float[]` or `bool`) and each `multi_output` entry a
    registered scalar, on every method shape, exactly as on the command
    line. A function keeps its own spellings (`float64[M]`, and `str` on a
    variable-output function).
