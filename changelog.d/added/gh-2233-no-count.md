- **`no_count`: a void-input `variable_output` method can be `f()`**
    (gh-2233). A `variable_output` method with `arg_type = "void"` and no
    params always got a synthesized Python `count` and a `size_t n` in its
    kernel, and no key removed them, so a kernel that writes at most one
    result sized by the object alone -- a spectrogram's `flush`, a PSD
    readout -- carried a keyword its callers had to pass for nothing and a
    slot its C had to `(void)`. `no_count = true` (`jm method --no-count`)
    drops both: the kernel is `fn(state, out[, max_out])`, its
    `<fn>_max_out(state)` takes the state alone and sizes every call, and
    the binding, both `.pyi` writers and the runtime `__doc__` offer no
    `count` -- only the optional `out=`, checked against that bound as
    before. `pass_capacity` keeps its exact allocation there (the bound
    cannot miss a count the call does not have), and the key composes with
    `out_cols`, `none_on_empty`, `count_type` with `error_negative`,
    `exact_max_out` and `multi_output`. It is refused at load where there is
    no synthesized count to drop -- without `variable_output`, with an
    input, with params, beside `count_default` or `count_name` -- and by
    `apply` when the header's `_max_out` still takes a count. A method that
    does not declare it is untouched.
