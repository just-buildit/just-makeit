- **`--multi-output` is refused on a method shape that cannot carry it**
    (gh-1540). A `--batch` method, a list of records (`--result-field`) and
    a `--single` record have no slot for extra outputs. jm stored
    `multi_output` on them and then generated nothing from it: no `*outN`
    parameter, no binding, no stub, so the declared output silently
    vanished. `jm method` now refuses the combination, and so does
    `jm apply` for a manifest that declares it. The error names the fix:
    drop `multi_output`, or return the values from a plain or
    `--variable-output` method, which carry each one as a trailing
    `<T> *outN`.
