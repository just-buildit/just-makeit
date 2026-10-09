- **`out_cols`: a `variable_output` method can return a matrix** (gh-2115). A
    kernel whose result is fixed-width rows -- a spectrogram's `nfft` bins, a
    frame of `n` samples -- filled a flat buffer, and every array jm allocated
    for it was 1-D, so the width had nowhere to be declared and the caller had
    to reshape. `out_cols = "state->nfft"` (or an integer; `jm method --out-cols`
    with the expression) now makes the binding return
    `(count / out_cols, out_cols)` on both the allocating path and `out=`. A
    caller-supplied `out=` must be 2-D with that many columns, and a kernel
    count that is not a whole number of rows is a `RuntimeError`. The kernel's
    contract is
    unchanged: it still sees a flat buffer and counts in elements, and
    `_max_out()` still answers in elements. Refused at load where it cannot
    apply: without `variable_output`, and with `multi_output`, `record_dtype`
    or `batch`. A method that does not declare it is untouched.
