- **Every array parameter's stub states exactly its declared type, from one
    helper** (gh-1724, gh-1819). An array argument's `.pyi` annotation is now
    `npt.NDArray[np.<dtype>]` of the dtype the manifest declares, on every
    face and in both stub generators: a constructor argument (required,
    `default = "[]"`, `--array-arg`, and `| None` for optional dispatch), a
    method or module-function parameter, a variable-output method's `x` and
    `out=`, the `steps()` input and its `out=`, an array property's setter, a
    handle or capsule method's arrays, and a composer stream field. A
    dtype-dispatch (`real_type`) array names both dtypes. A 1-D one-byte
    integer INPUT also admits `bytes | bytearray | memoryview`, the byte
    buffer jm reads directly (gh-1700); a writable (`out` / `mutable`) array
    stays exactly the ndarray (gh-1733). Before this each face spelled its
    own, and they disagreed: a standalone constructor said `npt.ArrayLike`
    where the module stub of the same object said `NDArray[...]`, a module
    stub dropped an `--array-arg` from the signature altogether, `steps()`
    left a byte array without its byte-buffer widening (gh-1819), a handle
    method said `NDArray[Any]` whatever it declared, and a standalone
    variable-output method over a scalar element said `x: float` for an
    argument its binding reads as an array. **This deliberately narrows the
    static type below the runtime:** a list, a tuple, a byte buffer or an
    ndarray of another safely castable dtype still works when run, but a type
    checker now asks for the declared ndarray (gh-1821, which asked to admit
    a list, is closed by this decision). The helper is
    `_types.array_param_annotation`; `tests/test_gh1724_array_init_stub.py`
    reads every array parameter of a project scaffolded with each face, plus
    the handle, capsule and composer stubs, and refuses any not spelled by
    it, and runs mypy over them.
