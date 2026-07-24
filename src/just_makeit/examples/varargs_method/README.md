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

---

## 3. Implement

Three stubs need bodies:

- `filter_step` in `native/inc/filter/filter_core.h` — multiply input by gain.
- `filter_configure` in `native/src/filter/filter_configure_core.c` — parse
  the `gain=` keyword argument and write it to state.
- `filter_current_gain` in `native/src/filter/filter_core.c` — return
  `state->gain`.

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
configure_c = pathlib.Path("native/src/filter/filter_configure_core.c")
configure_c.write_text((STEPS / "03_configure.c").read_text())
print(f"patched {configure_c}")

# -- 3. Implement the typed filter_current_gain reader in filter_core.c --
core = pathlib.Path("native/src/filter/filter_core.c")
core_text = core.read_text()
current_gain_re = re.compile(
    r"/\* <<IMPLEMENT: current_gain >> \*/\n"
    r"double\s*\nfilter_current_gain\(filter_state_t \*state\)\n\{.*?\}",
    re.DOTALL,
)
current_gain_impl = (
    "double\n"
    "filter_current_gain(filter_state_t *state)\n"
    "{\n"
    "    return state->gain;\n"
    "}"
)
if current_gain_re.search(core_text):
    core.write_text(current_gain_re.sub(current_gain_impl, core_text))
    print(f"patched {core}")
else:
    print("filter_current_gain: already patched or stub changed — skipping")
```

`filter_step` — one multiply:

```c
static inline float
filter_step (const filter_state_t *state, float x)
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
#include "filter/filter_core.h"
#include <Python.h>

PyObject *
filter_configure (PyObject *self, PyObject *args, PyObject *kwargs)
{
  typedef struct
  {
    PyObject_HEAD;
    filter_state_t *handle;
  } Obj;
  filter_state_t *state = ((Obj *)self)->handle;
  if (!state)
    {
      PyErr_SetString (PyExc_RuntimeError, "destroyed");
      return NULL;
    }
  double       gain     = state->gain;
  static char *kwlist[] = { "gain", NULL };
  if (!PyArg_ParseTupleAndKeywords (args, kwargs, "|d", kwlist, &gain))
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

### Document once, in C — rich stubs and a runnable doctest

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `create()` or a *header-declared* method flows
straight into the generated `.pyi` docstring, and a `@code` block becomes a
**runnable doctest**.

This is exactly where the `--varargs` trade-off shows up. `configure()`'s
binding lives in `filter_configure_core.c` — a `PyObject *` file, not the
header — so jm has no declaration to attach docs to, and its stub stays the
bare `configure(*args, **kwargs) -> Any`. The typed `current_gain()`, declared
in `filter_core.h`, is fully documentable. Add a comment to it — the `@code`
doctest deliberately drives `configure()` so both faces of the object are
exercised from one example:

```c
/**
 * @brief Return the filter's current gain coefficient.
 *
 * The typed, self-documenting companion to the flexible varargs
 * configure(): configure() writes the gain, current_gain() reads it
 * back.
 * @return The gain most recently set by the constructor or configure().
 * @code
 * >>> from my_filter import Filter
 * >>> f = Filter(gain=1.0)
 * >>> f.configure(gain=6.0)
 * >>> f.current_gain()
 * 6.0
 * @endcode
 */
double filter_current_gain(filter_state_t *state);
```

`just-makeit apply` re-derives the stub, and `src/my_filter/filter.pyi` now
carries the full numpy-style docstring — including the `@code` block as an
`Examples` doctest:

```python
    def current_gain(self) -> float:
        """Return the filter's current gain coefficient.

        Returns
        -------
        float
            The gain most recently set by the constructor or configure().

        Examples
        --------
        >>> from my_filter import Filter
        >>> f = Filter(gain=1.0)
        >>> f.configure(gain=6.0)
        >>> f.current_gain()
        6.0

        """
```

That doctest is not decoration: it runs against the *built* extension, so if
the kernel ever drifts from its documented example the build fails. Pass `-v`
to watch every `>>>` line execute:

```termynal
$ python -m doctest -v src/my_filter/filter.pyi
{d}Trying:{/d}
    f = Filter(gain=1.0)
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    f.configure(gain=6.0)
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    f.current_gain()
{d}Expecting:{/d}
    6.0
{g}ok{/g}
{d}...{/d}
{g}Test passed.{/g}
```

In CI the whole suite is driven at once with
`pytest --doctest-glob='*.pyi'`.

The enrichment is scripted (it stamps the project's package name into the
doctest import automatically):

```sh
python3 .steps/04b_doxygen.py
just-makeit apply
```

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

# current_gain() reads back what configure() set (the typed companion)
f.configure(gain=6.0)
assert f.current_gain() == 6.0

print("configure: PASSED")
```

`configure()` accepts `gain=` as a keyword or as a positional — both work
because `PyArg_ParseTupleAndKeywords` handles either calling convention.
Calling it with no arguments (`f.configure()`) is explicitly supported by the
`|` prefix in the format string and leaves the gain unchanged.
