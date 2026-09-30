- **An array param can say where text goes** (gh-1756). A `str_hint` on an
    array init param, method param, module-function param or handle method
    arg is appended to gh-1700's refusal of a `str`:
    `TypeError: sync must be an array of numbers, not str: build bits from text with field_bits()`. The converter gains one entry point,
    `jm_array_arg_hint`, which `jm_array_arg` now calls with `NULL`; a param
    without the key keeps its four-argument call, so its binding and its
    message are unchanged. The hint is escaped into a C string literal and
    passed as text, never as a format. `load` refuses a `str_hint` that is
    not a non-empty string, one on a param that is not an array, and one on
    an `out` buffer or a `strict` method's param, where it could never be
    shown. `jm apply` now replays it for a module function, and `jm status`
    reports a sacred fragment rendered before a declared `str_hint`.
