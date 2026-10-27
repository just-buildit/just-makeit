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

Read per STATEMENT (a call clang-format wraps is one), with strings and
comments masked; a local holding the state (``void *_jm_h = self->handle;``)
is tracked, so a use through it counts. ``tests/test_order_scan.py`` holds
every one of those to a synthetic function, positive and negative.

Known false negatives (the compiled probes in ``test_gh2380_*_compiled`` are
the truth anchors for them):

* a conversion CONV does not name: any call that runs Python but is not in
  the list (``PyObject_Length``, ``PyList_GetItem`` on a subclass, a
  ``Py_DECREF`` that runs a ``__del__``); a new helper prefix needs a CONV
  entry the day it is written;
* a use that is neither a call nor a ``->`` access of the held state: the
  state copied into a struct and passed on, or a pointer cast the aliasing
  regex does not read;
* a guard that is not one of the spellings in GUARD, which makes the scan
  louder (a false POSITIVE), never quieter;
* a function not shaped ``static <ret>\\nname(params)\\n{`` (a non-static or a
  macro-generated one), which is not read at all.
"""

from __future__ import annotations

import re

CONV = re.compile(
    r"PyArg_Parse|PyObject_Call|PySequence_|PyObject_IsTrue|PyNumber_|"
    r"PyLong_As|PyFloat_As|PyComplex_As|PyObject_GetAttr|PyObject_GetIter|"
    r"PyIter_Next|jm_array_arg|PyArray_FROM|PyObject_RichCompare|"
    r"PyObject_Str|PyObject_Bytes|PyObject_GetBuffer|PyObject_Length|"
    r"PyObject_Size|PyObject_GetItem|PyObject_SetItem|PyObject_Hash|"
    r"PyObject_Repr|PyObject_IsInstance|PyMapping_|_attach_|_coerce_"
)
GUARD = re.compile(
    r"if \((?:!?self->(?:handle|h|state|closed|destroyed)\b|"
    r".*self->(?:destroyed|closed)\b|!?w->destroyed\b)|_get_wrap\("
)
STATE = re.compile(r"\b(?:self|w)->(?:handle|h|state)\b")
FUNC = re.compile(
    r"\nstatic (?:PyObject \*|int|void|Py_ssize_t)\n(\w+)"
    r"\(((?:[^()]|\([^()]*\))*)\)\n\{\n"
    r"(.*?)\n\}(?=\n)",
    re.S,
)


_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
#: ``void *_jm_h = self->handle;`` -- a local that carries the state past a
#: conversion, so a later ``k(_jm_h, ...)`` is a use of it.
_ALIAS = re.compile(
    r"^(?:[\w ]+?[\s*]+)?(\w+)\s*=\s*(?:\([\w ]+\s*\*\)\s*)?"
    r"(?:self|w)->(?:handle|h|state)\s*;$"
)


def statements(body: str) -> "list[str]":
    """The body as statements, with strings and comments masked.

    A call clang-format wraps (``k(`` newline ``self->handle, v);``) is one
    statement, not two lines, so the state argument is seen where it is; a
    word inside a string or a comment is neither a conversion nor a use.
    """
    body = _COMMENT.sub(" ", body)
    body = _STRING.sub('""', body)
    out: "list[str]" = []
    cur: "list[str]" = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        cur.append(s)
        if s.endswith((";", "{", "}")):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def events(body: str) -> str:
    ev = []
    aliases: "set[str]" = set()
    for s in statements(body):
        m = _ALIAS.match(s)
        if m:
            aliases.add(m.group(1))
            continue
        if GUARD.search(s):
            ev.append("G")
        elif CONV.search(s):
            ev.append("C")
        elif not s.startswith(("if", "return NULL", "return -1")):
            # a call handed the state, or a field read / written through it
            held = STATE.search(s) is not None or any(
                re.search(rf"\b{re.escape(a)}\b", s) for a in aliases
            )
            if held and ("(" in s or "->" in s):
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
