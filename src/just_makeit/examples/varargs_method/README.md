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

Or with `pip`, which also works on Python 3.9 and 3.10 (the installer
needs 3.11+). It installs just-makeit, then builds the toolchain venv at
`/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

---

## 1. Scaffold

```sh
just-makeit new va_filter \
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
cd va_filter
just-makeit method filter configure --varargs
```

Three files carry the interesting changes — the manifest fragment
`objects/filter.toml`, the `.pyi` stub, and the benchmark harness are also
updated, as they are after every mutating command:

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
 *   typedef struct { PyObject_HEAD; va_filter_filter_state_t *handle; } Obj;
 *   va_filter_filter_state_t *state = ((Obj *)self)->handle;
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "va_filter/filter/filter_core.h"

/* <<IMPLEMENT: configure(*args, **kwargs)
 * Parse args/kwargs and return a PyObject *.
 * Return NULL on error (exception must be set).
 */
PyObject *
va_filter_filter_configure(PyObject *self, PyObject *args, PyObject *kwargs)
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
just-makeit method filter current_gain --return-type double
```

`current_gain()` reads the gain back. Being header-declared, its `@brief`,
`@return`, and a `@code` doctest flow straight into the `.pyi` (see step 3).

---

## 3. Implement

Three stubs need bodies:

- `va_filter_filter_step` in `native/inc/va_filter/filter/filter_core.h` — multiply input by gain.
- `va_filter_filter_configure` in `native/src/filter/filter_configure_core.c` — parse
  the `gain=` keyword argument and write it to state.
- `va_filter_filter_current_gain` in `native/src/filter/filter_core.c` — return
  `state->gain`.

`va_filter_filter_step` — one multiply:

```c
static inline float
va_filter_filter_step (const va_filter_filter_state_t *state, float x)
{
  return (float)(state->gain * x);
}
```

`va_filter_filter_configure` — parse `gain=` with `PyArg_ParseTupleAndKeywords`
(this is the whole of `filter_configure_core.c`):

```c
/*
 * filter_configure_core.c — varargs Python binding for filter.configure().
 *
 * Compiled into the Python extension DSO, not the pure-C core.
 * To access the C state inside this function:
 *   typedef struct { PyObject_HEAD; va_filter_filter_state_t *handle; } Obj;
 *   va_filter_filter_state_t *state = ((Obj *)self)->handle;
 */
#define PY_SSIZE_T_CLEAN
#include "va_filter/filter/filter_core.h"
#include <Python.h>

PyObject *
va_filter_filter_configure (PyObject *self, PyObject *args, PyObject *kwargs)
{
  typedef struct
  {
    PyObject_HEAD;
    va_filter_filter_state_t *handle;
  } Obj;
  va_filter_filter_state_t *state = ((Obj *)self)->handle;
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

Paste those bodies in by hand, or let the script below do it; it is the one
this example's own test runs. It reads the two C snippets from its own
directory, so save all three files — `03_patch.py`, `03_step.c` and
`03_configure.c` — in the directory you ran `just-makeit new` from, next to
`va_filter/` rather than inside it: `just-makeit apply` reads every `.c` file
under the project, and refuses a second definition of a name it derives, such
as `va_filter_filter_step`. Then, from the project root:

```sh
python3 ../03_patch.py
```

```python
"""Patch the step, configure and current_gain stubs with implementations.

Save this script, 03_step.c and 03_configure.c together in the directory
above the project, then run it from the project root (va_filter/):
    python3 ../03_patch.py
"""

import pathlib
import re

STEPS = pathlib.Path(__file__).parent

# -- 1. Patch the inline va_filter_filter_step in filter_core.h -------------------
header = pathlib.Path("native/inc/va_filter/filter/filter_core.h")
step_impl = (STEPS / "03_step.c").read_text(encoding="utf-8")
step_re = re.compile(
    r"static inline float\s*\nva_filter_filter_step"
    r"\(const va_filter_filter_state_t \*state, float x\)\n\{.*?\}",
    re.DOTALL,
)
text = header.read_text(encoding="utf-8")
if step_re.search(text):
    header.write_text(step_re.sub(step_impl.strip(), text), encoding="utf-8")
    print(f"patched {header}")
else:
    print("va_filter_filter_step: already patched or stub changed — skipping")

# -- 2. Replace filter_configure_core.c with the full implementation ----
configure_c = pathlib.Path("native/src/filter/filter_configure_core.c")
configure_c.write_text(
    (STEPS / "03_configure.c").read_text(encoding="utf-8"), encoding="utf-8"
)
print(f"patched {configure_c}")

# -- 3. Implement the typed va_filter_filter_current_gain reader in filter_core.c --
core = pathlib.Path("native/src/filter/filter_core.c")
core_text = core.read_text(encoding="utf-8")
current_gain_re = re.compile(
    r"/\* <<IMPLEMENT: current_gain >> \*/\n"
    r"double\s*\nva_filter_filter_current_gain\(va_filter_filter_state_t \*state\)\n\{.*?\}",
    re.DOTALL,
)
current_gain_impl = (
    "double\n"
    "va_filter_filter_current_gain(va_filter_filter_state_t *state)\n"
    "{\n"
    "    return state->gain;\n"
    "}"
)
if current_gain_re.search(core_text):
    core.write_text(
        current_gain_re.sub(current_gain_impl, core_text), encoding="utf-8"
    )
    print(f"patched {core}")
else:
    print(
        "va_filter_filter_current_gain: already patched or stub changed — skipping"
    )
```

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
 * >>> from va_filter import Filter
 * >>> f = Filter(gain=1.0)
 * >>> f.configure(gain=6.0)
 * >>> f.current_gain()
 * 6.0
 * @endcode
 */
double va_filter_filter_current_gain(va_filter_filter_state_t *state);
```

The enrichment is scripted. The script also replaces the scaffold `@brief` on
`va_filter_filter_create()`, which becomes the class docstring, and stamps the
project's package name into the doctest import. Save it as `04b_doxygen.py`
next to `va_filter/`, as above:

```python
"""Enrich the sacred ``filter_core.h`` with Doxygen so the generated ``.pyi``
carries rich docstrings and a runnable doctest.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. A
``@code`` block on a *typed named method* becomes a runnable doctest, which
``pytest --doctest-glob='*.pyi'`` executes against the built extension.

Note the split of responsibilities here (varargs vs. typed):

* ``configure()`` is a ``--varargs`` method — its binding lives in the sacred
  ``filter_configure_core.c`` (a ``PyObject *`` file), *not* in the header, so
  ``jm`` cannot attribute a Doxygen block to it and its ``.pyi`` stub stays the
  flexible ``(*args, **kwargs) -> Any``. That is the trade-off of ``--varargs``:
  an open-ended signature, but no header-derived docs or doctest.
* ``current_gain()`` is a plain typed method declared *in the header*, so its
  ``@brief``/``@return``/``@code`` flow straight into a numpy-style docstring
  with a runnable ``Examples`` block. The doctest deliberately exercises the
  varargs ``configure()`` too, tying both faces of the object together.

Run this after the methods are declared and their bodies patched; a follow-up
``jm apply`` regenerates the ``.pyi`` from these comments.

Usage (saved in the directory above the project, run from its root):
    python3 ../04b_doxygen.py
"""

from __future__ import annotations

import pathlib
import re
import sys

HEADER = pathlib.Path("native/inc/va_filter/filter/filter_core.h")


def _project_name() -> str:
    """Read ``[project] name`` from just-makeit.toml (the Python package name).

    The doctest imports ``from <package> import Filter``; deriving the name
    here keeps the enrichment correct whatever the project was scaffolded as.
    """
    toml = pathlib.Path("just-makeit.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', toml)
    if not m:
        print("ERROR: [project] name not found in just-makeit.toml")
        sys.exit(1)
    return m.group(1)


# A real one-line @brief on create() replaces jm's trivial scaffold brief and
# becomes the class docstring summary. (@param/@return are kept for C readers;
# gain is a plain state var, so its description is documented generically in the
# .pyi Parameters regardless — the summary is what changes.)
CREATE_BLOCK = (
    "/**\n"
    " * @brief A single-tap gain stage, retunable at runtime via configure().\n"
    " *\n"
    " * step() multiplies each input sample by the current gain; configure()\n"
    " * retunes that gain in place through a flexible **kwargs binding.\n"
    " * @param gain  Initial gain (default: 1.0).\n"
    " * @return Heap-allocated state, or NULL on allocation failure.\n"
    " * @note Caller must call va_filter_filter_destroy() when done.\n"
    " */\n"
)

# Doxygen for the typed named method. The @code block becomes a runnable
# Examples doctest in the .pyi; its output must match the built extension.
# ``<<PKG>>`` is filled from the project name so the import line is correct
# whatever the project was scaffolded as.
CURRENT_GAIN_BLOCK = (
    "/**\n"
    " * @brief Return the filter's current gain coefficient.\n"
    " *\n"
    " * The typed, self-documenting companion to the flexible varargs\n"
    " * configure(): configure() writes the gain, current_gain() reads it\n"
    " * back.\n"
    " * @return The gain most recently set by the constructor or configure().\n"
    " * @code\n"
    " * >>> from <<PKG>> import Filter\n"
    " * >>> f = Filter(gain=1.0)\n"
    " * >>> f.configure(gain=6.0)\n"
    " * >>> f.current_gain()\n"
    " * 6.0\n"
    " * @endcode\n"
    " */\n"
)

CURRENT_GAIN_DECL = (
    "double va_filter_filter_current_gain(va_filter_filter_state_t *state);"
)


def main() -> None:
    text = HEADER.read_text(encoding="utf-8")

    # 1. Swap the scaffold create() brief for a real one.
    scaffold_re = re.compile(
        r"/\*\*\n \* @brief Create a filter instance\..*?"
        r"(?=va_filter_filter_state_t \*va_filter_filter_create)",
        re.DOTALL,
    )
    text, n = scaffold_re.subn(CREATE_BLOCK, text, count=1)
    if n != 1:
        print(
            "ERROR: va_filter_filter_create scaffold brief not found",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Prepend the Doxygen block above the bare current_gain declaration,
    #    stamping the real package name into the doctest import line.
    if CURRENT_GAIN_DECL not in text:
        print(
            f"ERROR: declaration not found: {CURRENT_GAIN_DECL!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    block = CURRENT_GAIN_BLOCK.replace("<<PKG>>", _project_name())
    text = text.replace(CURRENT_GAIN_DECL, block + CURRENT_GAIN_DECL, 1)

    HEADER.write_text(text, encoding="utf-8")
    print(f"enriched {HEADER}")


if __name__ == "__main__":
    main()
```

Then, from the project root:

```sh
python3 ../04b_doxygen.py
just-makeit apply
```

`just-makeit apply` re-derives the stub, and `src/va_filter/filter.pyi` now
carries the full numpy-style docstring — including the `@code` block as an
`Examples` doctest:

```python
    def current_gain(self) -> float:
        """Return the filter's current gain coefficient.

        The typed, self-documenting companion to the flexible varargs
        configure(): configure() writes the gain, current_gain() reads it back.

        Returns
        -------
        float
            The gain most recently set by the constructor or configure().

        Examples
        --------
        >>> from va_filter import Filter
        >>> f = Filter(gain=1.0)
        >>> f.configure(gain=6.0)
        >>> f.current_gain()
        6.0

        """
```

That doctest is not decoration: run against the *built* extension, it fails
the moment the kernel drifts from its documented example. A generated
project's `make test` does not run `.pyi` doctests (this example's own test
does, with `pytest --doctest-glob='*.pyi'`), so to make it a gate in your
project add `PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/` to
your test step. Once step 4 has built the extension, pass `-v` to watch every
`>>>` line execute:

```termynal
$ PYTHONPATH=src python -m doctest -v src/va_filter/filter.pyi
{d}...{/d}
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

---

## 4. Build and test

```sh
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
from va_filter import Filter

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

Save it as `05_demo.py` next to `va_filter/`, like the step 3 scripts, and run
it from the project root (it imports the extension from `src/`):

```sh
python3 ../05_demo.py
# configure: PASSED
```

`configure()` accepts `gain=` as a keyword or as a positional — both work
because `PyArg_ParseTupleAndKeywords` handles either calling convention.
Calling it with no arguments (`f.configure()`) is explicitly supported by the
`|` prefix in the format string and leaves the gain unchanged.
