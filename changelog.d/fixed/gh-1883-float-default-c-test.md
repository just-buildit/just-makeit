- **A float or complex state default like `0.1` passes its own tests**
    (gh-1883). `jm new fd --object g --state a:float:0.1` scaffolded a
    project whose generated C test failed on the first `make test`: the
    field stores `(float)0.1`, and the test asserted
    `fd_g_get_a(obj) == 0.1`, comparing it against the double `0.1`, after
    construction and again after `reset()`. A `float _Complex` given
    `0.1 + 0.2 * I` failed both suites the same way, since the Python test
    compared complex values exactly. The C test now compares against the
    value the field holds, `(float)(0.1)`, the conversion the assignment
    applies, so the check stays exact for every float and complex type and
    every spelling of the default; the Python test compares complex values
    within `approx`, as it already did floats. `jm apply` refreshes a
    generated Python test; an existing project's C test is the author's and
    is not rewritten, so cast its default checks the same way by hand.
