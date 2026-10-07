- **A key an `[[<obj>.array_args]]` row does not read is reported, not
    silently kept** (gh-2008). The `--array-arg name:dtype` rows were the one
    object table the unknown-key walk never visited, and their reader takes
    only `name` and `type` / `dtype`, so anything else -- gh-2004's
    `rank = 1`, a misspelt `dtype` -- was accepted, kept and did nothing,
    with no warning: the array still flattened through `PyArray_SIZE`. Each
    such key now draws the same `warning:` line as an unknown key on every
    other table, naming the object and the row. The row does not grow the
    shape keys: a `rank` or `elements_per_sample` written on it is reported
    with the table that honours it, `[[<obj>.init_params]]`, where a
    constructor array typed `"float[]"` takes both.
