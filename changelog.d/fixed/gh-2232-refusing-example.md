- **A method that declares a way to refuse claims no result in its runtime
    example** (gh-2232). The doctest jm writes into a method's `__doc__`
    printed the return type's zero as the call's output, and a
    `variable_output` result's dtype after it. Both are true of the
    scaffold's stub and a guess about any real kernel. For a method that
    declares `error_negative`, `error_sentinel` or `error_on_empty`, that
    guess is exactly what the declaration says may not happen: doppler's
    `flush(np.zeros(4))` showed `0` over a kernel that refuses a zero row.
    Under `error_on_empty` the scaffold's own stub refused it too. gh-869
    already stopped the guess for `status_return`. Every refusal
    `declared_raise` reads now gets that treatment: the example shows the
    call and expects no output, so `>>> y = obj.flush(...)` with no line
    after it. A value is bound to `y` rather than echoed, so the line still
    passes whenever the call does. A method that cannot refuse keeps its
    result. The `.pyi` synthesizes no method example, so the two faces
    agree. `tests/test_gh2232_refusing_example.py` reads the refusal keys
    from `declared_raise` and refuses a claimed result in each shape one
    can be declared on.
