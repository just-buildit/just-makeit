## 2. Add a `--varargs` method

```{02_varargs.sh}
```

Three files change:

| File | Role |
| ---- | ---- |
| `native/src/filter/filter_configure_core.c` | Sacred — implement the body here. Compiled into the Python DSO so it may use `<Python.h>` directly. |
| `native/src/filter/filter_ext.c` | Regenerated — adds `extern` declaration and a `METH_VARARGS \| METH_KEYWORDS` entry in `PyMethodDef`. |
| `native/src/filter/CMakeLists.txt` | Surgically updated — `filter_configure_core.c` spliced into `Python3_add_library(...)`. |

The sacred file already contains everything needed to access component state:

```c
/* To access the C state inside this function:
 *   typedef struct { PyObject_HEAD; filter_state_t *handle; } Obj;
 *   filter_state_t *state = ((Obj *)self)->handle;
 */
PyObject *
filter_configure(PyObject *self, PyObject *args, PyObject *kwargs)
{
    (void)self; (void)args; (void)kwargs;
    Py_RETURN_NONE;
}
```

Unlike `--param` methods, `--varargs` passes the raw Python `args` and `kwargs`
tuples directly to C — no `PyArg_ParseTuple` is generated for you.  The
binding lives one layer above the pure-C core and can call any public C
function declared in `filter_core.h`.
