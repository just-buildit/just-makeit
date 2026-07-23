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


# The **opaque-bytes handler** (gh-565): a Python ``bytes`` (any read-only
# bytes-like) crosses into C as a borrowed ``(const void *, size_t)`` pair via
# the ``y#`` PyArg format. Unlike the path handler there is NO release step —
# ``y#`` does not create a new reference; it borrows the object's internal
# buffer, valid for the duration of the call (the args tuple keeps the object
# alive). The C callee must COPY the bytes before returning, exactly as it must
# for a path. Used for a ``type = "bytes"`` init-param that expands to a
# ``(const void *blob, size_t blob_len)`` constructor argument pair — the input
# twin of a ``bytes``-returning method. Requires ``PY_SSIZE_T_CLEAN`` (defined
# in every generated ext), so the ``#`` length target is a ``Py_ssize_t``.

# The C constructor parameter type an opaque-bytes arg presents: a borrowed
# buffer the callee must copy before returning.
BYTES_C_TYPE = "const void *"


def bytes_decl(name: str) -> list[str]:
    """Binding locals for an opaque-bytes arg — the borrowed buffer pointer and
    its length. ``y#`` fills both; neither needs releasing. Unindented; the
    caller adds its own indent."""
    return [
        f"const char *{name} = NULL;  /* borrowed bytes buffer */",
        f"Py_ssize_t {name}_len = 0;",
    ]


def bytes_fmt() -> str:
    """PyArg format code for an opaque-bytes arg — ``y#`` fills the pointer and
    length targets returned by :func:`bytes_addr`."""
    return "y#"


def bytes_addr(name: str) -> str:
    """PyArg address fragment: the buffer pointer and length targets ``y#``
    writes (two comma-separated items, mirroring an array's addr pair)."""
    return f"&{name}, &{name}_len"


def bytes_call_exprs(name: str) -> str:
    """Expressions passed to the C call — the borrowed buffer as ``const void *``
    and its length as ``size_t`` (two args, like a 1-D array). The callee MUST
    copy the buffer before returning; the borrow lives only for the call."""
    return f"(const void *){name}, (size_t){name}_len"
