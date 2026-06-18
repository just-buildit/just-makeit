"""
_coerce.py — shared argument-coercion primitives for generated CPython glue.

A few coercions are emitted identically by more than one generator. Centralize
them here so the generators cannot drift.

The **file/path handler** (gh-353): a Python ``str | os.PathLike`` crosses into C
as a borrowed ``PyBytes`` via ``PyUnicode_FSConverter`` (the ``O&`` form), the C
side receives a plain ``const char *`` it must COPY during the call, and the
borrow is released only AFTER the call returns (the gh-219 use-after-free trap).
Both the handle generator (:mod:`_handle`, which coerces a ``create_arg`` /
``method`` path) and the module-function generator (:mod:`_render`, which coerces
a ``jm function`` path param) emit exactly this shape — so it lives here once.
"""

from __future__ import annotations

# The C function parameter type a path arg presents to user C code: the callee
# gets a borrowed C string and must copy it before returning (see path_release).
PATH_C_TYPE = "const char *"


def path_decl(name: str) -> str:
    """Binding local for a path arg — a borrowed ``PyBytes`` (NULL until parsed
    by ``PyUnicode_FSConverter``). Unindented; the caller adds its own indent."""
    return f"PyObject *{name} = NULL;  /* fspath -> bytes */"


def path_fmt() -> str:
    """PyArg format code for a path arg — ``O&`` drives :func:`path_addr`."""
    return "O&"


def path_addr(name: str) -> str:
    """PyArg address fragment: the ``PyUnicode_FSConverter`` converter and its
    target object."""
    return f"PyUnicode_FSConverter, &{name}"


def path_call_expr(name: str) -> str:
    """Expression passed to the C call — the borrowed string. The callee MUST
    copy it before returning; the borrow is released right after the call."""
    return f"PyBytes_AS_STRING({name})"


def path_release(name: str) -> str:
    """Release the path borrow. Emitted AFTER the C call has copied the string
    (gh-219), and on every pre-call error path. ``Py_XDECREF`` is NULL-safe, so
    it is correct even when ``PyUnicode_FSConverter`` never ran."""
    return f"Py_XDECREF({name});"
