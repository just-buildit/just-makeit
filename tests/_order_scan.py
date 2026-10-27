"""Find a generated binding function with the gh-2273 / gh-2380 window.

A method's liveness guard runs first and its arguments are converted after
it. Converting can run Python (``__index__``, ``__float__``, ``__complex__``,
``__array__``), and Python can tear the object down, so the kernel is then
called on a state that is gone. The fix is a recheck after the last
conversion (`_inflight.recheck`); this module finds the places that lack one,
by reading the C jm generated, not the code that generated it. A new emitter
is therefore covered the day it is written, which a gate over one emitter's
source cannot say (gh-2381's was exactly that, and missed ``step()`` and every
setter).

The model, per function body, line by line:

``G``  a liveness guard: ``if (!self->handle)``, ``if (self->closed)``,
       ``if (self->destroyed``, ``if (w->destroyed)``, ``_get_wrap(``
``C``  a call that can run Python: ``PyArg_Parse*``, ``PyNumber_*``,
       ``PyLong_As*``, ``jm_array_arg``, a ``_attach_`` helper...
``U``  a call handing the state to the author's C: ``self->handle`` /
       ``self->h`` / ``self->state`` / ``w->state`` as an argument

A function is flagged when a ``U`` follows a ``C`` with no ``G`` between
them, and a ``G`` came before the ``C``: checked, converted, used.
"""

from __future__ import annotations

import re

CONV = re.compile(
    r"PyArg_Parse|PyObject_Call|PySequence_|PyObject_IsTrue|PyNumber_|"
    r"PyLong_As|PyFloat_As|PyComplex_As|PyObject_GetAttr|PyObject_GetIter|"
    r"PyIter_Next|jm_array_arg|PyArray_FROM|PyObject_RichCompare|"
    r"PyObject_Str|PyObject_Bytes|PyObject_GetBuffer|_attach_|_coerce_"
)
GUARD = re.compile(
    r"if \((?:!?self->(?:handle|h|state|closed|destroyed)\b|"
    r".*self->(?:destroyed|closed)\b|!?w->destroyed\b)|_get_wrap\("
)
STATE = re.compile(r"\b(?:self|w)->(?:handle|h|state)\b")
FUNC = re.compile(
    r"\nstatic (?:PyObject \*|int|void|Py_ssize_t)\n(\w+)\(([^)]*)\)\n\{\n"
    r"(.*?)\n\}\n",
    re.S,
)


def events(body: str) -> str:
    ev = []
    for ln in body.splitlines():
        s = ln.strip()
        if GUARD.search(s):
            ev.append("G")
        elif CONV.search(s):
            ev.append("C")
        elif (
            STATE.search(s)
            and "(" in s
            and not s.startswith(("if", "/*", "*", "//"))
        ):
            ev.append("U")
    return "".join(ev)


def window(seq: str) -> bool:
    """True when a use follows a conversion that follows a guard, unchecked."""
    guard = conv = False
    for e in seq:
        if e == "G":
            guard, conv = True, False
        elif e == "C" and guard:
            conv = True
        elif e == "U" and conv:
            return True
    return False


def unrechecked(c_text: str) -> "list[tuple[str, str]]":
    """``[(function, event string)]`` for every function with the window."""
    out = []
    for m in FUNC.finditer(c_text):
        name, _sig, body = m.groups()
        seq = events(body)
        if window(seq):
            out.append((name, seq))
    return out
