- **A module function's status names its exception** (gh-1614).
    `check_return = true` raised `RuntimeError("<fn> failed (rc=N)")` for
    every non-zero status, so a bad argument (`DP_ERR_INVALID`) could not be
    `ValueError` nor an allocation failure `MemoryError`. A `check_return`
    function now takes the `status_errors` table methods already have
    (gh-1418): `--status-error DP_ERR_INVALID:ValueError[:message]` on
    `jm function`, repeatable, or `status_errors` rows in the manifest. The
    binding switches on the returned status, a row raises its exception, and
    a status with no row keeps the `check_return` error. A message may name
    the function's scalar params (`{n}`). Under `why = true` (gh-1706) the
    row picks the class and the sentence the C function wrote is the text.
    Rows on a function whose binding reads no status -- no `check_return`,
    or a self-sizing output whose refusal is a zero count -- are refused, as
    are rows the method face refuses.
