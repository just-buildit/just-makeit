- **A no-argument method that returns records builds warning-clean and
    refuses an argument** (gh-1959). A method with `arg_type = "void"` and
    no params that returned one record (`single`), or a list of them, was
    bound as `(self, PyObject *args)` under `METH_VARARGS` and never read
    `args`, so `-Wall -Wextra` reported an unused parameter in the
    standalone `_ext.c` and in a module's fragment, adopted or not. The call
    also took any positional arguments and dropped them, so `obj.fer(1)`
    returned the record although both `.pyi` writers declare `fer(self)`.
    Both shapes are now `METH_NOARGS` with `Py_UNUSED(ignored)`, like every
    other no-argument wrapper, and `obj.fer(1)` raises `TypeError`.
    `jm apply` regenerates a standalone `_ext.c` and an adopted fragment
    (`fragment = "generated"`); one not yet adopted lists the wrapper and
    its `PyMethodDef` table as differing in `jm adopt --check`, to accept by
    name.
