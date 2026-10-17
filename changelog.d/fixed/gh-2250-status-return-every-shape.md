- **`status_return` works beside `--out-type` and `--multi-output`, and is
    refused where there is no status to read** (gh-2250). Beside an output
    the binding called the kernel without it, so the project did not
    compile; it now passes the outputs, raises on a non-zero status, and
    returns them (a tuple when several), with both `.pyi` files and
    `help()` saying so. On a `--single` or list-of-records method, a
    `--variable-output` result or a `--borrow` -- whose kernels return a
    record, a count or a pointer -- the key was stored and ignored, so a
    refusing kernel's error code became a result; `jm method` and `apply`
    now refuse it, saying what to declare instead. A non-integer return
    is refused too: `void` did not compile and a float was truncated. A
    fixed-output `--multi-output` method no longer leaks a reference per
    returned value, and its `.pyi` and `help()` now annotate the tuple it
    returns (they said the scalar and an ndarray).
