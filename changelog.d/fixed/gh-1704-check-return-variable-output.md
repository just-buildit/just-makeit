- **`check_return` on a self-sizing function raises on a zero count**
    (gh-1704). A `variable_output` function returns the count it wrote, and
    `check_return` was tested after that branch, so the key was accepted and
    read by nobody: a refusal came back as a valid, empty array (or `""`).
    The count is now the status it reads. 0 raises `RuntimeError` naming the
    function, after the output is released, and the stub keeps its output
    type rather than `-> None`. The two shapes that never read the C return,
    a void `variable_output` and a caller-sized `out_type`, now refuse the
    key at generation with a message saying which shape to use, rather than
    ignoring it.
