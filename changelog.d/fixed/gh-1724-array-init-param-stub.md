- **An array constructor argument's stub no longer type-checks a `str`**
    (gh-1724). A standalone object's `.pyi` annotated every array init-param
    `npt.ArrayLike` (required, `default = "[]"`, optional dispatch and
    `--array-arg` alike), and `npt.ArrayLike` admits `str`. The binding
    refuses one (gh-1700), so `Fld("0101")` passed mypy and failed only when
    run. The module stub of the same object already said `NDArray[...]`, but
    its optional-array argument did not admit the `bytes` a byte array
    takes, and an `--array-arg` object in a module got
    `(self, /, *args, **kwargs)`. Both stub generators now spell every array
    constructor argument with one helper, `_types.array_param_annotation`:
    `NDArray` of the element dtype, plus `bytes | bytearray | memoryview` for
    `uint8_t[]` / `int8_t[]`, and both dtypes for a dtype-dispatch
    (`real_type`) array. A standalone stub that relied on `npt.ArrayLike` to
    accept a plain list now asks for an ndarray, as method parameters and
    module objects already did. A test renders each shape through both
    generators and requires them to agree, and mypy refuses a `str` call
    against each.
