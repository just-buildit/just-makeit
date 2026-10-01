- **`jm apply` keeps every key on a module function's param** (gh-1760).
    `apply` rebuilt each function param from a fixed tuple
    `(name, type, out, default, enum, doc, str_hint)`. Any key without a
    slot never reached the manifest the binding is rendered from, and
    `apply` still exited 0. `rank` and `elements_per_sample` (gh-805 §C)
    were two such keys, so a function's array param lost its rank guard and
    its interleave divisor. The kernel was then handed `n` elements where it
    counts `n / k` samples. An object method's param kept both. The replay
    now passes each param's manifest row whole, as gh-432 did for method
    params. A new test sends a representative value for every key in
    `FUNCTION_PARAM_KEYS` through `apply` and fails on a vocabulary key it
    has no value for, and a build calls the function to check both the guard
    and the divisor. `jm script` still drops these keys with no NOTE, filed
    as gh-1765.
