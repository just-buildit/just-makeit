- **A status message naming a float slot raises the exception it declares**
    (gh-1785). A `status_errors` row whose message named a float param or
    property -- on a borrow (gh-1426) or a `check_return` function
    (gh-1614) -- compiled, then raised
    `SystemError: invalid format string` when it fired, because
    `PyErr_Format` has no float conversion. The value is now written as
    Python's `repr` of it (`gain 1.1 is out of range`), and jm refuses at
    generation time any conversion `PyErr_Format` would reject.
