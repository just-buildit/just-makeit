- **A method's or function's `T[][]` param is refused, not flattened
    (gh-1908).** `--param "a:float[][]"` (also `--out-param` and
    `--arg-type`) exited 0 and bound a flat `float *a, size_t a_len`; the
    binding accepted a (2, 3) array and flattened it. Only a constructor's
    `T[][]` passes two extents. One predicate, `_types.array_type_error`, is
    now asked by every flag and by `apply`'s manifest check, so the CLI
    and a hand-written row agree: one `error:` line naming `float[]` and
    `--init-param`, the tree untouched.
