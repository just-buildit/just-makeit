"""`_order_scan` on synthetic C: what it must flag and what it must not.

The scan is the ratchet for every kind of gh-2380, so its false negatives
are the failure it exists to prevent. Each case is a whole generated-looking
function, positive (flagged) and negative (not).
"""

from __future__ import annotations

import pytest
from _order_scan import unrechecked

HEAD = "static PyObject *\nW_f(WObject *self, PyObject *args)\n{\n"
TAIL = "\n}\n"
GUARD = (
    "    if (!self->handle) {\n"
    '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
    "        return NULL;\n"
    "    }\n"
)


def _fn(body: str) -> str:
    return "\n" + HEAD + body + TAIL


def _flagged(body: str) -> bool:
    return bool(unrechecked(_fn(body)))


POSITIVE = {
    "guard-convert-use": (
        GUARD + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        "    w_set_k(self->handle, v);\n"
    ),
    # clang-format puts the state argument on its own line.
    "state-on-a-continuation-line": (
        GUARD + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        "    w_set_k(\n"
        "        self->handle, v);\n"
    ),
    "state-after-a-wrapped-first-argument": (
        GUARD + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        "    w_set_k((int)\n"
        "            v,\n"
        "        self->handle);\n"
    ),
    "use-through-a-hoisted-local": (
        GUARD + "    void *_jm_h = self->handle;\n"
        '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        "    w_set_k(_jm_h, v);\n"
    ),
    "conversion-in-a-helper-attach": (
        GUARD + "    if (_attach_buf(self, obj) < 0) return NULL;\n"
        "    w_run(self->handle);\n"
    ),
    "conversion-in-a-helper-coerce": (
        GUARD + "    PyArrayObject *a = _coerce_x(obj);\n"
        "    w_run(self->handle, PyArray_DATA(a));\n"
    ),
    # generic protocol calls run `__len__` / `__getitem__` / `__hash__`
    "conversion-by-length": (
        GUARD + "    Py_ssize_t n = PyObject_Length(seq);\n"
        "    w_run(self->handle, n);\n"
    ),
    "conversion-by-getitem": (
        GUARD + "    PyObject *e = PyObject_GetItem(seq, key);\n"
        "    w_run(self->handle, e);\n"
    ),
    "array-conversion": (
        GUARD
        + "    PyArrayObject *a = jm_array_arg(o, NPY_FLOAT, 0, 'x', NULL);\n"
        "    w_run(self->handle, PyArray_DATA(a));\n"
    ),
    # a field written through the handle has no call parenthesis
    "field-write-through-the-handle": (
        GUARD + '    if (!PyArg_Parse(value, "i", &v)) return NULL;\n'
        "    self->handle->p4 = v;\n"
    ),
    "field-read-through-a-hoisted-local": (
        GUARD + "    w_state_t *_s = self->handle;\n"
        '    if (!PyArg_Parse(value, "i", &v)) return NULL;\n'
        "    int x = _s->p4;\n"
    ),
    "wrapper-state": (
        "    if (w->destroyed) return NULL;\n"
        "    double v = PyFloat_AsDouble(o);\n"
        "    k_set(w->state, v);\n"
    ),
    "a-guard-before-the-conversion-only": (
        GUARD + "    PyObject *o = PyNumber_Long(x);\n"
        "    int ok = w_get(self->handle);\n"
    ),
}

NEGATIVE = {
    "recheck-before-use": (
        GUARD
        + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        + GUARD
        + "    w_set_k(self->handle, v);\n"
    ),
    "recheck-releases-first": (
        GUARD
        + "    PyArrayObject *a = jm_array_arg(o, NPY_FLOAT, 0, 'x', NULL);\n"
        "    if (!self->handle) {\n"
        "        Py_DECREF(a);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
        "    w_run(self->handle, PyArray_DATA(a));\n"
    ),
    "recheck-on-a-continuation-line-use": (
        GUARD
        + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        + GUARD
        + "    w_set_k(\n"
        "        self->handle, v);\n"
    ),
    "convert-then-guard-then-use": (
        '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        + GUARD
        + "    w_set_k(self->handle, v);\n"
    ),
    "no-conversion": GUARD
    + "    return PyLong_FromLong(w_get(self->handle));\n",
    "no-use": (
        GUARD + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        "    Py_RETURN_NONE;\n"
    ),
    "hoist-after-the-recheck": (
        GUARD
        + '    if (!PyArg_ParseTuple(args, "i", &v)) return NULL;\n'
        + GUARD
        + "    void *_jm_h = self->handle;\n"
        "    w_set_k(_jm_h, v);\n"
    ),
}


@pytest.mark.parametrize("name", sorted(POSITIVE))
def test_flagged(name):
    assert _flagged(POSITIVE[name]), name


@pytest.mark.parametrize("name", sorted(NEGATIVE))
def test_not_flagged(name):
    assert not _flagged(NEGATIVE[name]), name


def test_a_parameter_list_with_a_macro_call_is_still_a_function():
    """``Py_UNUSED(closure)`` has a ``)`` inside the parameter list: every
    getset setter has one, and the first scanner skipped them all."""
    text = (
        "\nstatic int\n"
        "W_setprop_p(WObject *self, PyObject *value, void *Py_UNUSED(c))\n"
        "{\n" + GUARD + '    if (!PyArg_Parse(value, "i", &v)) return -1;\n'
        "    self->handle->p = v;\n}\n"
    )
    assert [n for n, _ in unrechecked(text)] == ["W_setprop_p"]


def test_functions_with_no_blank_line_between_them_are_all_scanned():
    """A match must not eat the newline the next function starts with: the
    first scanner read every second one of a getset run."""
    ok = "\nstatic int\nW_a(WObject *self)\n{\n    return 0;\n}"
    bad = _fn(POSITIVE["guard-convert-use"]).rstrip("\n")
    text = ok + bad + "\nstatic int\nW_c(WObject *self)\n{\n    return 0;\n}\n"
    assert [n for n, _ in unrechecked(text)] == ["W_f"]
    assert [
        n for n, _ in unrechecked(bad + bad.replace("W_f", "W_g") + "\n")
    ] == [
        "W_f",
        "W_g",
    ]


def test_the_name_of_the_function_is_reported():
    got = unrechecked(_fn(POSITIVE["guard-convert-use"]))
    assert [n for n, _ in got] == ["W_f"]


def test_a_comment_or_a_string_is_not_a_use_or_a_conversion():
    body = (
        GUARD
        + "    /* PyArg_ParseTuple then w_set_k(self->handle, v) */\n"
        + '    PyErr_SetString(PyExc_RuntimeError, "PyArg_Parse self->handle");\n'
    )
    assert not _flagged(body)
