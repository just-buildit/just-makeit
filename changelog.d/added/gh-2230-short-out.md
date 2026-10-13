- **`short_out`: a backpressure kernel takes an `out=` smaller than
    `max_out()`** (gh-2230). A `variable_output` method's `out=` had to hold at
    least what the binding would have allocated, so a short buffer raised
    `out has N elements, need >= M` -- right for a kernel that merely clamps,
    and fatal for one whose contract is backpressure: it writes the rows that
    fit, takes only the input that produced them and reports how much, and no
    caller could ever present it a short buffer. `short_out = true`
    (`jm method --short-out`) declares that contract: the buffer is then held
    to its shape alone -- any length, `0` included; under `out_cols` any number
    of rows of that width -- the kernel is handed its true size, and the result
    is shaped to what it wrote. How much input was taken stays the object's
    own property, so the caller resumes with
    `rows = s.push(x, out=buf); x = x[s.consumed:]`. It requires
    `pass_capacity` and is refused at load without it (and without
    `variable_output`); a method that does not declare it keeps the minimum,
    word for word. Both `.pyi` faces and the runtime `__doc__` say the buffer
    has no minimum.
