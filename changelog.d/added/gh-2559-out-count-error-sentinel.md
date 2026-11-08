- **A method filling an `out=` array can refuse with a sentinel in place of
    its count** (gh-2559). A method whose kernel writes a caller's
    `out = true` array param and returns the `size_t` count it wrote now
    takes `error_sentinel` (`jm method --error-sentinel SIZE_MAX`), as a
    `variable_output` count has since gh-2012: a return equal to it raises
    `error` with `error_message`, `0` stays an ordinary "nothing yet", and
    any other count returns as an `int` as before. Before, the key was
    refused there ("this method returns none"), so a kernel refusing a
    member its configuration does not serve handed Python
    `18446744073709551615`. It is the same rule and the same emitter: the
    count must be `size_t` (a signed return refuses with `--error-negative`
    instead), a result that replaces the return (`--status-return`,
    `--out-type`, `--multi-output`, `--single`, `--borrow`) refuses it, the
    `.pyi` and the runtime docstring name the raise, a view may reword its
    message, `jm apply` and `jm script` carry it, and a sacred module
    fragment rendered before it was declared is reported.
