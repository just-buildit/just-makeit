- **An array element with no numpy dtype is refused at the declaration
    (gh-2132).** `bool[]`, `int[]` and `long double _Complex[]` passed the
    manifest's type check (it asked `SUPPORTED_TYPES`, the CLI's `--param`
    asked `SUPPORTED_ARRAY_CTYPES`), so `jm apply` died with
    `KeyError: 'bool'`; `jm method --arg-type 'int[]'` saved its row and
    then crashed, leaving every later command dead; a `bool` record alias
    spoken as `s[]` did the same; and so did an `--init-param` `a:bool[]`.
    All of them ask `_types.array_type_error` now (a method's params and
    `arg_type`, a function's params, an init-param of an object or a view,
    and a handle or capsule array), and the refusal names the spelling that
    works (`uint8_t[]`, `int32_t[]`, `double _Complex[]`).
