- **A scaffolded test and benchmark construct with every defaulted argument
    left out** (gh-2556). They spelled out every init param, restating each
    default in Python, and a `default_raw` has no Python spelling: a
    `size_t` one rendered as `noise_hi=...` (Ellipsis, so every generated
    test failed with a `TypeError`) and a `NAN` one as `cn0_dbhz=.0`, a
    different object from the default one. On an object with a state
    struct a second builder of the same call restated them as the type's
    zero, which passed while testing the wrong object. Now only a required
    parameter is spelled (its `example_value` where given, gh-1105), plus
    any defaulted one carrying an `example_value`; the rest are omitted so
    the binding applies its own default, in the test, the benchmark and
    the `.pyi` example alike. The C smoke test and benchmark, which cannot
    omit an argument, pass the `default_raw` itself.
