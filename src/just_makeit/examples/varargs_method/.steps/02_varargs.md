## 2. Add a `--varargs` method

```{02_varargs.sh}
```

Three files carry the interesting changes — `just-makeit.toml`, the `.pyi`
stub, and the benchmark harness are also regenerated, as they are after every
mutating command:

| File | Role |
| ---- | ---- |
| `native/src/filter/filter_configure_core.c` | Sacred — implement the body here. Compiled into the Python DSO so it may use `<Python.h>` directly. |
| `native/src/filter/filter_ext.c` | Regenerated — adds `extern` declaration and a `METH_VARARGS \| METH_KEYWORDS` entry in `PyMethodDef`. |
| `native/src/filter/CMakeLists.txt` | Surgically updated — `filter_configure_core.c` spliced into `Python3_add_library(...)`. |

The sacred file already contains everything needed to access component state,
and marks the spot to fill in:

```c
/*
 * filter_configure_core.c — varargs Python binding for filter.configure().
 *
 * Compiled into the Python extension DSO, not the pure-C core.
 * To access the C state inside this function:
 *   typedef struct { PyObject_HEAD; filter_state_t *handle; } Obj;
 *   filter_state_t *state = ((Obj *)self)->handle;
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "filter/filter_core.h"

/* <<IMPLEMENT: configure(*args, **kwargs)
 * Parse args/kwargs and return a PyObject *.
 * Return NULL on error (exception must be set).
 */
PyObject *
filter_configure(PyObject *self, PyObject *args, PyObject *kwargs)
{
    (void)self; (void)args; (void)kwargs;
    Py_RETURN_NONE;
}
```

Unlike `--param` methods, `--varargs` passes the raw `args` tuple and `kwargs`
dict straight to C — no `PyArg_ParseTuple` is generated for you.  The
binding lives one layer above the pure-C core and can call any public C
function declared in `filter_core.h`.

### A typed companion, for contrast

`--varargs` buys an open-ended signature, but it costs documentability: the
binding lives in `filter_configure_core.c` (a `PyObject *` file), so jm has no
header declaration to attach docs to and the `.pyi` stub stays the bare
`configure(*args, **kwargs) -> Any`.  Add a plain typed method — declared *in*
`filter_core.h` — so we have something the header can fully document:

```sh
cd my_filter
just-makeit method filter current_gain --return-type double
```

`current_gain()` reads the gain back. Being header-declared, its `@brief`,
`@return`, and a `@code` doctest flow straight into the `.pyi` (see step 3).
