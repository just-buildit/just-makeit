# varargs_method example

A `filter` object whose runtime configuration is updated through
`configure(**kwargs)`.  Typed `--param` flags work well when the parameter
set is fixed at code-generation time; `--varargs` is the right tool when it
is open-ended, mixed-type, or evolves independently of the scaffold.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example varargs_method
# varargs_method: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold

```sh
just-makeit new my_filter \
    --object filter \
    --state "gain:double:1.0" \
    --arg-type float \
    --return-type float
```

One state variable — `gain` — gives the Python constructor a keyword argument
(`Filter(gain=2.0)`) and a C-side field that `step()` and `configure()` both
share.

---

## 2. Add a `--varargs` method

```sh
cd my_filter
just-makeit method filter configure --varargs
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

---

## 3. Implement

Two stubs need bodies:

- `filter_step` in `native/inc/filter/filter_core.h` — multiply input by gain.
- `filter_configure` in `native/src/filter/filter_configure_core.c` — parse
  the `gain=` keyword argument and write it to state.

```python
"""Patch filter_step and filter_configure stubs with implementations.

Run from the project root (my_filter/):
    python3 .steps/03_patch.py
"""

import pathlib
import re

STEPS = pathlib.Path(__file__).parent

# -- 1. Patch the inline filter_step in filter_core.h -------------------
header = pathlib.Path("native/inc/filter/filter_core.h")
step_impl = (STEPS / "03_step.c").read_text()
step_re = re.compile(
    r"static inline float\s*\nfilter_step"
    r"\(const filter_state_t \*state, float x\)\n\{.*?\}",
    re.DOTALL,
)
text = header.read_text()
if step_re.search(text):
    header.write_text(step_re.sub(step_impl.strip(), text))
    print(f"patched {header}")
else:
    print("filter_step: already patched or stub changed — skipping")

# -- 2. Replace filter_configure_core.c with the full implementation ----
configure_c = pathlib.Path(
    "native/src/filter/filter_configure_core.c"
)
configure_c.write_text((STEPS / "03_configure.c").read_text())
print(f"patched {configure_c}")
```

`filter_step` — one multiply:

```c
static inline float
filter_step(const filter_state_t *state, float x)
{
    return (float)(state->gain * x);
}
```

`filter_configure` — parse `gain=` with `PyArg_ParseTupleAndKeywords`:

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

PyObject *
filter_configure(PyObject *self, PyObject *args, PyObject *kwargs)
{
    typedef struct { PyObject_HEAD; filter_state_t *handle; } Obj;
    filter_state_t *state = ((Obj *)self)->handle;
    if (!state) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    double gain = state->gain;
    static char *kwlist[] = {"gain", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|d", kwlist, &gain))
        return NULL;
    state->gain = gain;
    Py_RETURN_NONE;
}
```

`PyArg_ParseTupleAndKeywords` accepts the same format characters as
`PyArg_ParseTuple`.  The `|` marks everything that follows as optional, so
`f.configure()` with no arguments is valid and leaves the gain unchanged.
The static `kwlist` array controls which keyword names are accepted and
enables `TypeError` on unknown keywords.

---

## 4. Build and test

```sh
cd my_filter
make && make test
```

`filter_configure_core.c` uses `<Python.h>` and compiles into the Python
extension target only, so the C-only CTest binary links without Python.
The two translation units stay cleanly separated: pure-C core in the OBJECT
library, Python-aware binding compiled directly into the DSO.

---

## 5. Use from Python

```python
import sys
sys.path.insert(0, "src")
from my_filter import Filter

f = Filter(gain=1.0)
assert f.step(2.0) == 2.0

f.configure(gain=0.5)
assert f.step(2.0) == 1.0

# positional also accepted (PyArg_ParseTupleAndKeywords handles both)
f.configure(2.0)
assert f.step(1.0) == 2.0

# no args: gain unchanged
f.configure()
assert f.step(1.0) == 2.0

print("configure: PASSED")
```

`configure()` accepts `gain=` as a keyword or as a positional — both work
because `PyArg_ParseTupleAndKeywords` handles either calling convention.
Calling it with no arguments (`f.configure()`) is explicitly supported by the
`|` prefix in the format string and leaves the gain unchanged.
