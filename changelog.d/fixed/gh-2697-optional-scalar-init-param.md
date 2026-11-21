- **An init-param `optional = true` on anything but an array is refused at
    load (gh-2697).** `optional` is array dispatch (the array's presence
    selects `create_fn`), and the CLI already refused it on any other type,
    but a manifest could still declare it on a scalar: the module `.pyi`
    then read `window: int | None = None` and the docstring `int or None`
    while the binding parsed a plain integer seeded with `0`, so `None`
    raised `TypeError` and an omitted argument reached `create()` as `0`.
    The refusal names the row and what to declare instead: `default`, or
    `default_raw` for a named C constant; a capsule param already takes
    `None` unless it declares `required = true`.
