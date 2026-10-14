- **A `strict` refusal names the argument the caller passed** (gh-2271).
    A `strict` input is refused rather than converted, and the refusal
    named the binding's C local rather than the keyword: `prod(x[::2])`
    raised "in must be a 1-D C-contiguous array". It now says
    `x must be ...`, as the converting path and the `.pyi` already did,
    for a scalar-element input and a record input alike.
