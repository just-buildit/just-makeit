- **`out=` and `<m>_max_out` for a `variable_output` method with an input
    and params** (gh-2028). `run(x, gain)` -- an `arg_type` input with params
    beside it -- was the one single-output shape offered no `out=` buffer:
    its parse dropped the params until gh-1960, and after that `out` joining
    the Python arguments broke the header's `@param in` -> `x` match in the
    doc. Both are generated now, in the binding and both `.pyi` faces: `out`
    is the parse's trailing optional argument (`run(x, gain, out=None)`,
    positional or keyword, after a defaulted param's group too), sized from
    the input exactly as the same method without params is, and
    `<m>_max_out(len(x))` sizes it. An undersized buffer is refused before
    the kernel runs. A `manual_stub` entry for that `<m>_max_out` is now
    refused, with the instruction to drop it: the stub it declared is jm's
    own. `multi_output` is the one `variable_output` shape left without
    `out=`.
