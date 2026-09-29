- **An output size past `NPY_MAX_INTP` raises `OverflowError`** (gh-1710).
    A binding that sizes its output from a C value -- a function's
    `out_size`, a caller-sized `[M]` length, a function's list-of-records
    capacity, a method's `max_out()` or integer-param length, a borrowed
    view's count, a handle's `out_len_fn` -- cast it straight to `npy_intp`,
    so `SIZE_MAX` wrapped to `-1` and the caller saw numpy's
    `ValueError: negative dimensions are not allowed`. Two sites handed the
    callee a short buffer: a `str` output's `malloc(_cap + 1)` wrapped to
    `malloc(0)`, and a list-of-records `malloc(_max * sizeof(T))` wrapped for a large capacity.
    The size is now evaluated once into a `size_t` and refused before the
    cast or the arithmetic with
    `OverflowError("<name>: output of <n> elements is too large")`, after
    releasing any parsed input. Every site uses one emitter,
    `_coerce.output_size_c`.
