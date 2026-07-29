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


# The **caller-supplied output buffer** handler (gh-581): an `out=` argument is
# the caller saying "write into THIS array, do not allocate". Marshaling it with
# a bare ``PyArray_FROM_OTF(out_obj, NPY_X, …| NPY_ARRAY_WRITEABLE)`` quietly
# breaks that promise whenever the dtype does not already match: FROM_OTF casts
# into a NEW temporary, the kernel fills the temporary, and the temporary is
# freed on the way out — so the call returns a correct-looking result while the
# caller's buffer is never touched. The failure is invisible (a reuse-one-buffer
# streaming loop still reads correct return values), which is what makes it worth
# a hard guard rather than a doc note.
#
# Dtype is not the only way FROM_OTF can substitute a temporary. It is asked for
# ``NPY_ARRAY_C_CONTIGUOUS`` too, and a **strided** array satisfies the dtype
# check while still forcing a contiguous copy — same silent failure, different
# trigger (gh-604 follow-up). Measured on a generated `steps(x, out=)`:
#
#     big = np.zeros((4, 2), np.float32)
#     g.steps(np.arange(4, np.float32), out=big[:, 0])
#     big[:, 0]  ->  [0. 0. 0. 0.]      # never written; the return was a copy
#
# So the guard REQUIRES the exact output dtype **and C-contiguity** up front and
# rejects anything else. `PyArray_FROM_OTF` still runs afterwards to take the
# reference, but with both properties already proven it can no longer copy, so
# the array it returns is always the caller's own buffer.
#
# Alignment is deliberately NOT checked. NumPy's own `NPY_ARRAY_ALIGNED` only
# demands the dtype's natural alignment, which every ndarray already satisfies,
# so it would reject nothing; SIMD alignment is a performance matter, not a
# correctness one, and lives in `docs/memory-ownership.md` (a misaligned `out=`
# measured ~16% on FFT(4096)) rather than in a hard error.
#
# The guard is a strict tightening: code that relied on the cast or the copy
# was, by definition, not getting its buffer written.


def out_buffer_guard(
    obj_var: str,
    npy_enum: str,
    *,
    label: str = "out",
    decrefs: str = "",
    indent: str = "    ",
) -> str:
    """Emit the dtype + contiguity guard for a caller-supplied ``out=`` buffer.

    Every generator that lets a caller pass their own output array emits this
    identical check, so it lives here once (see the module note above for why
    the check has to exist at all).

    Parameters
    ----------
    obj_var : str
        Name of the borrowed ``PyObject *`` holding the caller's argument, as
        parsed by ``PyArg_ParseTuple*`` — e.g. ``"out_obj"``.
    npy_enum : str
        The required numpy type enum, e.g. ``"NPY_COMPLEX64"``. An array of any
        other dtype is rejected rather than cast.
    label : str, optional
        Name of the argument as the user typed it, used in the error message.
        Defaults to ``"out"``; the handle generator passes its declared output
        array's name instead.
    decrefs : str, optional
        C statements releasing anything already acquired on the success path,
        run before the early ``return NULL`` — e.g. ``"Py_DECREF(in_arr);"``.
        Empty when the guard is the first thing after argument parsing.
    indent : str, optional
        Leading whitespace for the emitted block. Four spaces at function scope,
        eight inside an ``if (out_obj && out_obj != Py_None)`` branch.

    Returns
    -------
    str
        A newline-terminated C block. Contains literal braces, so interpolate it
        into an f-string as a value (``f"{guard}"``) — never paste it into an
        f-string *literal*, where its braces would need doubling.

    Examples
    --------
    >>> print(out_buffer_guard("out_obj", "NPY_COMPLEX64"), end="")
        /* Require the exact dtype AND C-contiguity — either mismatch makes
         * the marshal write into a temp copy, not the caller's buffer. */
        if (!PyArray_Check(out_obj) ||
            PyArray_TYPE((PyArrayObject *)out_obj) != NPY_COMPLEX64 ||
            !PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj) ||
            !PyArray_ISWRITEABLE((PyArrayObject *)out_obj)) {
            PyErr_SetString(PyExc_TypeError,
                "out must be a writable, C-contiguous"
                " ndarray of the output dtype");
            return NULL;
        }

    A guard inside a branch, with an input array already owned:

    >>> print(out_buffer_guard("out_obj", "NPY_FLOAT32",
    ...                        decrefs="Py_DECREF(in_arr);",
    ...                        indent=" " * 8), end="")
            /* Require the exact dtype AND C-contiguity — either mismatch makes
             * the marshal write into a temp copy, not the caller's buffer. */
            if (!PyArray_Check(out_obj) ||
                PyArray_TYPE((PyArrayObject *)out_obj) != NPY_FLOAT32 ||
                !PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj) ||
                !PyArray_ISWRITEABLE((PyArrayObject *)out_obj)) {
                PyErr_SetString(PyExc_TypeError,
                    "out must be a writable, C-contiguous"
                    " ndarray of the output dtype");
                Py_DECREF(in_arr);
                return NULL;
            }
    """
    i = indent
    release = f"{i}    {decrefs}\n" if decrefs else ""
    return (
        f"{i}/* Require the exact dtype AND C-contiguity — either mismatch"
        f" makes\n"
        f"{i} * the marshal write into a temp copy, not the caller's"
        f" buffer. */\n"
        f"{i}if (!PyArray_Check({obj_var}) ||\n"
        f"{i}    PyArray_TYPE((PyArrayObject *){obj_var}) != {npy_enum} ||\n"
        f"{i}    !PyArray_IS_C_CONTIGUOUS((PyArrayObject *){obj_var}) ||\n"
        f"{i}    !PyArray_ISWRITEABLE((PyArrayObject *){obj_var})) {{\n"
        f"{i}    PyErr_SetString(PyExc_TypeError,\n"
        f'{i}        "{label} must be a writable, C-contiguous"\n'
        f'{i}        " ndarray of the output dtype");\n'
        f"{release}"
        f"{i}    return NULL;\n"
        f"{i}}}\n"
    )
