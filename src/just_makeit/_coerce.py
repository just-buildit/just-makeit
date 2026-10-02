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

import re

# The C function parameter type a path arg presents to user C code: the callee
# gets a borrowed C string and must copy it before returning (see path_release).
PATH_C_TYPE = "const char *"

# The Python annotation a path arg presents to a type checker — the other half
# of the same contract. `PyUnicode_FSConverter` accepts anything os.fspath()
# accepts, so every path surface (an object init-param, a handle create-arg or
# method, a `jm function` param) takes `str | os.PathLike` and must SAY so:
# gh-623 shipped a stub annotating an object init-param `str`, which made the
# tested, documented `Reader(pathlib.Path(...))` call an error to mypy. It sits
# beside PATH_C_TYPE so the C and Python faces of a path cannot drift apart.
# A stub using this must `import os` — see `_stubs._uses_os`.
PATH_PY_TYPE = "str | os.PathLike"


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


def out_buffer_guard_record(
    obj_var: str,
    dtype_fn: str,
    *,
    label: str = "out",
    decrefs: str = "",
    indent: str = "    ",
) -> str:
    """`out_buffer_guard` for a STRUCTURED result (gh-805 §E).

    The scalar guard compares ``PyArray_TYPE(...) != NPY_x``. A structured
    array's type num is ``NPY_VOID``, so that comparison cannot express "the
    record layout this method returns" — it would reject every correct buffer
    and, worse, the acquisition that follows it (``PyArray_FROM_OTF`` with a
    scalar enum) would silently **cast** a correctly-shaped structured array to
    that scalar type. That is the exact silent reinterpretation the ``out=``
    guard exists to prevent, and it is why the feature was carved out for
    ``record_dtype`` rather than shipped half-working.

    So the comparison is against the descriptor jm already generates:
    ``PyArray_EquivTypes`` against ``<sid>_get_dtype()``. Equivalence rather
    than identity, because a caller who rebuilds the same dtype from a list of
    fields has an equal-but-not-identical descr and is not wrong.

    The reference is released on every path. ``<sid>_get_dtype()`` returns a
    NEW reference, and only ``PyArray_NewFromDescr`` steals one — the guard
    does not, so it must not leak.

    Parameters
    ----------
    obj_var : str
        Borrowed ``PyObject *`` holding the caller's argument.
    dtype_fn : str
        Name of the generated descriptor accessor, e.g. ``"W_read_get_dtype"``.
        Emitted by :func:`_record.dtype_c`.
    label, decrefs, indent
        As :func:`out_buffer_guard`.

    Returns
    -------
    str
        A newline-terminated C block. Contains literal braces — interpolate it
        as a value, never inside an f-string literal.

    Examples
    --------
    >>> print(out_buffer_guard_record("out_obj", "W_read_get_dtype"), end="")
        /* Require the exact record dtype AND C-contiguity. Compared with
         * EquivTypes against the generated descr: a structured array's type
         * num is NPY_VOID, so a scalar enum cannot say what layout is
         * wanted, and coercing to one would silently reinterpret the
         * caller's buffer. */
        {
            PyArray_Descr *_want = W_read_get_dtype();
            if (!_want) {
                return NULL;
            }
            if (!PyArray_Check(out_obj) ||
                !PyArray_EquivTypes(
                    PyArray_DESCR((PyArrayObject *)out_obj), _want) ||
                !PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj) ||
                !PyArray_ISWRITEABLE((PyArrayObject *)out_obj)) {
                PyErr_Format(PyExc_TypeError,
                    "out must be a writable, C-contiguous ndarray of"
                    " dtype %R, not %R", _want,
                    PyArray_Check(out_obj)
                        ? (PyObject *)PyArray_DESCR((PyArrayObject *)out_obj)
                        : Py_None);
                Py_DECREF(_want);
                return NULL;
            }
            Py_DECREF(_want);
        }
    """
    i = indent
    release = f"{i}        {decrefs}\n" if decrefs else ""
    return (
        f"{i}/* Require the exact record dtype AND C-contiguity. Compared"
        f" with\n"
        f"{i} * EquivTypes against the generated descr: a structured array's"
        f" type\n"
        f"{i} * num is NPY_VOID, so a scalar enum cannot say what layout is\n"
        f"{i} * wanted, and coercing to one would silently reinterpret the\n"
        f"{i} * caller's buffer. */\n"
        f"{i}{{\n"
        f"{i}    PyArray_Descr *_want = {dtype_fn}();\n"
        f"{i}    if (!_want) {{\n"
        f"{release}"
        f"{i}        return NULL;\n"
        f"{i}    }}\n"
        f"{i}    if (!PyArray_Check({obj_var}) ||\n"
        f"{i}        !PyArray_EquivTypes(\n"
        f"{i}            PyArray_DESCR((PyArrayObject *){obj_var}), _want) ||\n"
        f"{i}        !PyArray_IS_C_CONTIGUOUS((PyArrayObject *){obj_var}) ||\n"
        f"{i}        !PyArray_ISWRITEABLE((PyArrayObject *){obj_var})) {{\n"
        f"{i}        PyErr_Format(PyExc_TypeError,\n"
        f'{i}            "{label} must be a writable, C-contiguous ndarray'
        f' of"\n'
        f'{i}            " dtype %R, not %R", _want,\n'
        f"{i}            PyArray_Check({obj_var})\n"
        f"{i}                ? (PyObject *)PyArray_DESCR("
        f"(PyArrayObject *){obj_var})\n"
        f"{i}                : Py_None);\n"
        f"{i}        Py_DECREF(_want);\n"
        f"{release}"
        f"{i}        return NULL;\n"
        f"{i}    }}\n"
        f"{i}    Py_DECREF(_want);\n"
        f"{i}}}\n"
    )


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


def array_rank_guard(
    pname: str,
    arr_var: str,
    rank: int,
    decrefs: str = "",
    fail: str = "return NULL;",
) -> str:
    """A ``PyArray_NDIM`` guard for an array param (gh-805 §C).

    ``PyArray_FROM_OTF`` with ``NPY_ARRAY_C_CONTIGUOUS`` accepts an array of
    **any** rank, and jm then measures it with ``PyArray_SIZE`` — the total
    element count. So a 2-D array handed to a parameter whose C contract is
    1-D does not fail; it flattens, and the kernel reads a buffer whose shape
    it was never told about. Nothing raises and nothing warns.

    Opt-in via ``rank`` on the param, deliberately. Flattening is the current
    behaviour and some callers rely on it — passing a C-contiguous 2-D block
    to a kernel that genuinely wants the flat run is legitimate — so an
    unconditional guard would break working trees. Declaring ``rank`` is the
    author saying which of the two this parameter is.

    Parameters
    ----------
    pname : str
        Parameter name, for the message the caller sees.
    arr_var : str
        The ``PyArrayObject *`` local to test.
    rank : int
        Required ``PyArray_NDIM``.
    decrefs : str
        Cleanup for arrays already acquired on this path, emitted before the
        bailout. The guard runs after *arr_var* is acquired, so it releases
        that one itself on top of these.
    fail : str
        ``return NULL;`` in a method wrapper, ``return -1;`` in an ``initproc``
        — the same split `capsule_new_c` draws, and for the same reason: a
        hard-coded ``return NULL`` inside an ``initproc`` compiles and reports
        success.

    Examples
    --------
    >>> print(array_rank_guard("h", "h_arr", 1), end="")
        if (PyArray_NDIM(h_arr) != 1) {
            PyErr_SetString(PyExc_ValueError,
                            "h must be a 1-D array");
            Py_DECREF(h_arr); return NULL;
        }
    """
    release = f"{decrefs} " if decrefs else ""
    return (
        f"    if (PyArray_NDIM({arr_var}) != {rank}) {{\n"
        f"        PyErr_SetString(PyExc_ValueError,\n"
        f'                        "{pname} must be a {rank}-D array");\n'
        f"        {release}Py_DECREF({arr_var}); {fail}\n"
        f"    }}\n"
    )


# The **array-argument converter** (gh-1700): how every generated binding turns
# the Python object a caller passed for a ``T[]`` parameter into an ndarray.
#
# It is ``PyArray_FROM_OTF`` -- numpy decides what an argument converts to,
# under the binding's own requirement flags -- plus one typing fix and one
# per-parameter opt-in:
#
#   * a byte buffer into a one-byte element type. numpy reads a ``bytes`` as
#     ONE scalar of a text dtype and casts it, so ``b"\x01\x00"`` into a
#     ``uint8_t[]`` was refused with ``invalid literal for int()`` -- although
#     ``bytes`` is the one Python type that already IS a byte buffer. For
#     ``uint8_t[]`` / ``int8_t[]`` any buffer-protocol object whose items are
#     one byte wide (``bytes``, ``bytearray``, ``memoryview``, ``array('B')``)
#     is read as its bytes, one element per byte, which is ``np.frombuffer``.
#     A buffer of wider items keeps numpy's element-wise conversion rather
#     than being reinterpreted byte by byte.
#   * a ``str`` is refused ONLY by a parameter that declares a ``str_hint``
#     (gh-1756, gh-1824), and the hint is appended to the refusal. Declaring
#     the hint IS the opt-in: such a parameter refuses what gh-1700 once
#     refused on every array -- a ``str``, and a ``bytes`` into an element
#     type wider than a byte, both of which numpy would parse as a number.
#     Every other array argument is numpy's: ``"0101"`` into a ``uint8_t[]``
#     is ``np.asarray("0101", dtype=np.uint8)``, the one element 101. What a
#     parameter accepts is a policy the parameter states, not one jm imposes
#     on every array of every project (gh-1824).
#
# It is ONE C function, emitted once per extension translation unit through
# ``ARRAY_ARG_C`` and called through ``array_arg`` from every generator that
# acquires an array argument -- init params, method params, module functions,
# ``step``/``steps`` input, array property setters, handle and capsule
# methods. A per-site guard is how a fix lands in four of those and not the
# fifth; ``tests/test_gh1700_array_arg_str_bytes.py`` refuses a generator that
# calls ``PyArray_FROM_OTF`` on an argument directly.
#
# It is emitted UNCONDITIONALLY, and ``jm_array_arg`` is marked ``unused``
# (gh-1747). ``jm_array_arg_hint`` (gh-1756) needs no mark: the wrapper
# always calls it, so clang never sees it uncalled, and marking it anyway is
# a change no build can tell from its absence. An extension with no array
# argument, or one whose every argument declares a ``str_hint``, never
# calls the wrapper, and clang reports an
# uncalled ``static inline`` defined in the main file (gcc does not), so a
# ``-Werror`` build failed. Emitting it only where a generated call site
# exists cannot be decided when the file is rendered: a module's ``_ext.c``
# ``#include``s its per-object fragments, which the author may hand-patch
# and jm then preserves, plus any ``*_prologue.c`` / ``*_extra.c`` hook, and
# the aggregator is rendered without reading any of them. The helper is
# that translation unit's shared infrastructure -- the role a ``static
# inline`` in a header plays, where neither compiler warns -- so the
# attribute says what is true of it: it may go uncalled.

ARRAY_ARG_FN = "jm_array_arg"

#: The same converter taking a ``str_hint`` (gh-1756). ``jm_array_arg`` is it
#: with ``NULL``: a param declaring no hint leaves a ``str`` to numpy.
ARRAY_ARG_HINT_FN = "jm_array_arg_hint"

#: The per-param manifest key that opts a parameter in to refusing a ``str``
#: (gh-1824), and names what to pass instead.
STR_HINT_KEY = "str_hint"

ARRAY_ARG_C = """\
#ifndef JM_ARRAY_ARG_DEFINED
#define JM_ARRAY_ARG_DEFINED
/* Convert a Python argument for an array parameter to an ndarray of
 * `typenum` meeting `requirements` -- PyArray_FROM_OTF, except that for a
 * one-byte element type a byte buffer (bytes, bytearray, memoryview) is its
 * bytes, one element per byte (gh-1700). `name` is the parameter, for the
 * message. `hint` is its declared str_hint, or NULL. Declaring one is the
 * opt-in to refusing text (gh-1824): a str, or a bytes numpy would parse as
 * a number, and a str's refusal ends with the hint (gh-1756). With NULL,
 * numpy converts a str as it converts anything else. Returns a new
 * reference, or NULL with an exception. */
static inline PyArrayObject *
jm_array_arg_hint(PyObject *obj, int typenum, int requirements,
                  const char *name, const char *hint)
{
    int one_byte = typenum == NPY_UINT8 || typenum == NPY_INT8;
    int text = PyUnicode_Check(obj) || (!one_byte && PyBytes_Check(obj));
    if (hint && text) {
        /* The hint says where text goes instead: a str's refusal only. */
        int say = PyUnicode_Check(obj);
        PyErr_Format(PyExc_TypeError,
                     "%s must be an array of numbers, not %.200s%s%s", name,
                     Py_TYPE(obj)->tp_name, say ? ": " : "",
                     say ? hint : "");
        return NULL;
    }
    if (one_byte && !PyArray_Check(obj) && PyObject_CheckBuffer(obj)) {
        PyObject *view = PyMemoryView_FromObject(obj);
        if (!view)
            return NULL;
        if (PyMemoryView_GET_BUFFER(view)->itemsize == 1) {
            PyObject *raw = PyArray_FromBuffer(
                view, PyArray_DescrFromType(typenum), -1, 0);
            Py_DECREF(view);
            if (!raw)
                return NULL;
            PyObject *arr = PyArray_FROM_OTF(raw, typenum, requirements);
            Py_DECREF(raw);
            return (PyArrayObject *)arr;
        }
        Py_DECREF(view);
    }
    return (PyArrayObject *)PyArray_FROM_OTF(obj, typenum, requirements);
}
/* `unused` (gh-1747): emitted into every extension translation unit,
 * including one that takes no array, or calls only jm_array_arg_hint above
 * -- which needs no mark, since this wrapper always calls it. */
#if defined(__GNUC__) || defined(__clang__)
__attribute__((unused))
#endif
static inline PyArrayObject *
jm_array_arg(PyObject *obj, int typenum, int requirements, const char *name)
{
    return jm_array_arg_hint(obj, typenum, requirements, name, NULL);
}
#endif /* JM_ARRAY_ARG_DEFINED */
"""


def array_arg(
    obj_var: str, npy_enum: str, flags: str, name: str, hint: str = ""
) -> str:
    r"""The C expression converting *obj_var* for array parameter *name*.

    Every generated acquisition of an array argument is this call to the
    ``ARRAY_ARG_C`` helper, never a bare ``PyArray_FROM_OTF`` (see the note
    above for the two inputs that differ). It evaluates to a new
    ``PyArrayObject *`` reference, or ``NULL`` with an exception set, exactly
    as the ``(PyArrayObject *)PyArray_FROM_OTF(...)`` it replaces.

    *hint* is the parameter's declared ``str_hint`` (gh-1756), read through
    `str_hint`. Declaring one is the opt-in to refusing a ``str``
    (gh-1824), and the hint is appended to that refusal, naming what to
    pass instead. It is escaped here into a C string literal, through the
    table every other manifest-authored C message uses. Empty -- every
    parameter declaring none -- keeps the four-argument ``jm_array_arg``
    call, which leaves a ``str`` to numpy's conversion.

    Examples
    --------
    >>> array_arg("bits_obj", "NPY_UINT8", "NPY_ARRAY_C_CONTIGUOUS", "bits")
    'jm_array_arg(bits_obj, NPY_UINT8, NPY_ARRAY_C_CONTIGUOUS, "bits")'
    >>> print(array_arg("s_obj", "NPY_UINT8", "0", "s", 'use "bits()"'))
    jm_array_arg_hint(s_obj, NPY_UINT8, 0, "s", "use \"bits()\"")
    """
    if not hint:
        return f'{ARRAY_ARG_FN}({obj_var}, {npy_enum}, {flags}, "{name}")'
    # Deferred: `_context` imports this module, so a top-level import of the
    # escape table would be circular.
    from ._context._diagnostics import _C_ESCAPES

    literal = hint.translate(_C_ESCAPES)
    return (
        f'{ARRAY_ARG_HINT_FN}({obj_var}, {npy_enum}, {flags}, "{name}",'
        f' "{literal}")'
    )


def str_hint(param) -> str:
    """A parameter's declared ``str_hint`` (gh-1756), or ``""``.

    The ONE reader every array-acquiring generator hands to `array_arg`, so
    no two faces can disagree about where the key lives. *param* is a
    manifest row (a dict: a method, function or handle-method param) or an
    init-param tuple from `_config.init_params`, whose slot 16 carries it.
    The value itself is checked at load, by `str_hint_errors`.

    Examples
    --------
    >>> str_hint({"name": "sync", "str_hint": "use field_bits()"})
    'use field_bits()'
    >>> str_hint({"name": "sync"})
    ''
    >>> str_hint(("sync", "uint8_t[]", ""))
    ''
    """
    if isinstance(param, dict):
        return param.get(STR_HINT_KEY) or ""
    return (param[16] if len(param) > 16 else "") or ""


#: A ``[[<obj>.state]]`` row with no generated accessor at all (gh-1761).
_OPAQUE_STATE = "an opaque state field"

#: Why a hint on each pre-empted row could never be shown. Every other entry
#: is refused as a non-ndarray before ``jm_array_arg`` runs.
_NEVER_SHOWN_WHY = {
    _OPAQUE_STATE: "which has no generated set_<name> to refuse a str",
}
_NEVER_SHOWN_DEFAULT = (
    "which must already be an ndarray and is refused with its own message "
    "before the hint could apply"
)


def str_hint_rows(cfg: dict) -> "list[tuple[str, dict, str, bool]]":
    """Every ``(table, row, pre-empted, is array)`` that can reach `array_arg`.

    An object's (and a view's) ``init_params``, its methods' ``params``, its
    ``state`` fields (gh-1761: an array field's ``set_<name>``), a module
    function's ``params`` and a handle module method's ``args``: the tables
    a ``str_hint`` is honoured on. *is array* is whether the row's ``type``
    is an array in that table's grammar -- ``T[]`` for a parameter, ``T[N]``
    for a state field. *pre-empted* names what refuses a non-ndarray BEFORE
    ``jm_array_arg`` runs, so a hint on that row could never be shown, or is
    ``""``:

    - ``"a strict method's input"`` -- gh-1426 B refuses rather than
      converts;
    - ``"a record param"`` -- an array of a declared ``[[<obj>.records]]``
      element is acquired by the record's dtype, which requires an ndarray
      of it (gh-1405). Decided by `_record.declared` over `_config.records`,
      the predicate the method binding itself uses;
    - ``"an opaque state field"`` -- it has no ``set_<name>`` at all.
    """
    from . import _config, _record
    from ._types import (
        array_elem_ctype,
        is_array_param_type,
        parse_array_type,
    )

    rows: "list[tuple[str, dict, str, bool]]" = []

    def add(
        where: str, params, why=lambda p: "", array=is_array_param_type
    ) -> None:
        rows.extend(
            (where, p, why(p), bool(array(str(p.get("type", "")))))
            for p in params or []
            if isinstance(p, dict)
        )

    for comp in _config.components(cfg):
        body = cfg[comp]
        if not isinstance(body, dict):
            continue
        add(f"[[{comp}.init_params]]", body.get("init_params"))
        # gh-1761: the declaration of an array field's `set_<name>`.
        add(
            f"[[{comp}.state]]",
            body.get("state"),
            lambda p: _OPAQUE_STATE if p.get("opaque") else "",
            parse_array_type,
        )
        records = _config.records(cfg, comp)

        def method_why(p: dict, strict: bool) -> str:
            ptype = str(p.get("type", ""))
            if is_array_param_type(ptype) and _record.declared(
                records, array_elem_ctype(ptype)
            ):
                return "a record param"
            return "a strict method's input" if strict else ""

        for m in body.get("methods") or []:
            if isinstance(m, dict):
                strict = bool(m.get("strict"))
                add(
                    f"[[{comp}.methods.params]]",
                    m.get("params"),
                    lambda p, s=strict: method_why(p, s),
                )
        for v in body.get("views") or []:
            if isinstance(v, dict):
                add(f"[[{comp}.views.init_params]]", v.get("init_params"))
    for mid, mod in (cfg.get("module") or {}).items():
        if not isinstance(mod, dict):
            continue
        for fn in mod.get("functions") or []:
            if isinstance(fn, dict):
                add(f"[[module.{mid}.functions.params]]", fn.get("params"))
        if mod.get("kind") == "handle":
            for m in mod.get("methods") or []:
                if isinstance(m, dict):
                    add(f"[[module.{mid}.methods.args]]", m.get("args"))
    return rows


def str_hint_errors(cfg: dict) -> "list[str]":
    """Every ``str_hint`` jm would not honour, as a message naming its row.

    gh-1756. Refused at load, where the row can still be named, because
    each of these would otherwise be accepted and then do nothing:

    - a value that is not a non-empty string. It becomes a C string literal,
      and ``str_hint = true`` has no text to append.
    - a ``str_hint`` on a parameter that is not an array. Only an array
      parameter's refusal of a ``str`` reads it.
    - one on an ``out`` / ``mutable`` / ``writable`` array, a ``strict``
      method's param or a record param. Each refuses anything that is not
      already an ndarray BEFORE ``jm_array_arg`` runs, with its own message.
    - one on an opaque state field, which has no ``set_<name>`` (gh-1761).
    - one on a ``[[<obj>.properties]]`` row (gh-1761). No property setter
      converts an array -- a property's type is a scalar, a container, a
      capsule, or a read-only ``buf_field`` view -- so the message names
      the ``[[<obj>.state]]`` row, whose array ``set_<name>`` does.

    Examples
    --------
    >>> bad = {"f": {"init_params": [
    ...     {"name": "n", "type": "int", "str_hint": "x"}]}}
    >>> str_hint_errors(bad)[0].split(":")[0]
    '[[f.init_params]] n'
    >>> bad["f"]["init_params"][0].update(type="uint8_t[]", str_hint=1)
    >>> "str_hint = 1 must be a non-empty string" in str_hint_errors(bad)[0]
    True
    >>> bad["f"]["init_params"][0]["str_hint"] = "use bits()"
    >>> str_hint_errors(bad)
    []
    """
    from ._config import components
    from ._types import param_writable

    errors: "list[str]" = []
    for where, p, pre_empted, is_array in str_hint_rows(cfg):
        if STR_HINT_KEY not in p:
            continue
        value = p[STR_HINT_KEY]
        name = p.get("name", "?")
        if not isinstance(value, str) or not value:
            errors.append(
                f"{where} {name}: str_hint = {value!r} must be a non-empty "
                "string -- the text appended to the parameter's refusal of "
                "a str, naming what to pass instead"
            )
        elif not is_array:
            errors.append(
                f"{where} {name}: str_hint is read only by an array "
                f"parameter's refusal of a str, and {name} is "
                f"{p.get('type', '?')!r}; drop the key"
            )
        elif param_writable(p) or p.get("writable") or pre_empted:
            what = pre_empted or "an out buffer"
            why = _NEVER_SHOWN_WHY.get(what, _NEVER_SHOWN_DEFAULT)
            errors.append(
                f"{where} {name}: str_hint is never shown on {what}, {why}; "
                "drop the key"
            )
    # gh-1761: a property is not a row `array_arg` can reach, so it is not
    # in `str_hint_rows`; the key there is the natural wrong guess for an
    # array field's setter, and accepted it would do nothing.
    for comp in components(cfg):
        body = cfg[comp]
        if not isinstance(body, dict):
            continue
        tables = [(f"[[{comp}.properties]]", body.get("properties"))]
        tables += [
            (f"[[{comp}.views.properties]]", v.get("properties"))
            for v in body.get("views") or []
            if isinstance(v, dict)
        ]
        for where, props in tables:
            for p in props or []:
                if isinstance(p, dict) and STR_HINT_KEY in p:
                    name = p.get("name", "?")
                    errors.append(
                        f"{where} {name}: str_hint is never shown on a "
                        "property -- no property setter converts an array. "
                        "An array state field's set_<name> does: declare "
                        f"the hint on its [[{comp}.state]] row"
                    )
    return errors


def input_array_acq(
    npy_enum: str = "",
    dtype_fn: str = "",
    *,
    obj_var: str = "in_obj",
    arr_var: str = "in_arr",
    flags: str = "NPY_ARRAY_C_CONTIGUOUS",
    fail: str = "return NULL;",
    strict: bool = False,
    expect: str = "",
    label: str = "",
    hint: str = "",
) -> str:
    """Acquire an input array as *arr_var*, C-contiguous.

    ONE emitter, because this was spelled five times -- four method shapes
    in ``_context/_methods`` (kwargs, plain, stream, list-of-records) and
    the named-param path in ``_context/_parse`` -- and gh-1405 needed a
    second acquisition form in every one of them. Five copies of "how does
    an input array arrive" is how the shapes come to disagree about it.

    Parameters
    ----------
    npy_enum : str
        The numpy typenum for a scalar element (``NPY_FLOAT32``). Uses
        ``PyArray_FROM_OTF``, which CASTS a compatible input.
    dtype_fn : str
        A record's cached descr builder (``<sid>_get_dtype``), for rows of
        the author's struct (gh-1405). Uses ``PyArray_FromAny``, which
        **steals** the descr reference -- what the builder's new reference
        is for -- and requires the input to match rather than casting it:
        a structured dtype has no meaningful cast, and quietly accepting
        the wrong one would read every row from the wrong bytes.
    obj_var, arr_var : str
        The borrowed ``PyObject *`` and the ``PyArrayObject *`` to define.
    flags : str
        Requirement flags; an ``out`` param adds ``NPY_ARRAY_WRITEABLE``.
    fail : str
        What to run on failure -- a param path releases the arrays it has
        already acquired first.
    label : str
        The parameter name a refusal names; defaults to *arr_var* less its
        ``_arr`` suffix. A method's primary input is the local ``in_arr``
        while the caller spells it ``x``.
    hint : str
        The parameter's ``str_hint`` (gh-1756), passed to `array_arg`.

    Examples
    --------
    >>> print(input_array_acq(npy_enum="NPY_FLOAT32", label="x"))
        PyArrayObject *in_arr =
            jm_array_arg(in_obj, NPY_FLOAT32, NPY_ARRAY_C_CONTIGUOUS, "x");
        if (!in_arr) { return NULL; }
    <BLANKLINE>
    >>> acq = input_array_acq(dtype_fn="Ring_write_x")
    >>> "PyArray_EquivTypes" in acq        # refuses, never reinterprets
    True
    >>> "PyArray_FromAny" in acq
    True
    """
    if bool(npy_enum) == bool(dtype_fn):
        raise ValueError(
            "input_array_acq wants exactly one of npy_enum / dtype_fn"
        )
    if dtype_fn:
        descr = f"_{arr_var.removesuffix('_arr')}_descr"
        label = arr_var.removesuffix("_arr")
        # The input's dtype must EQUAL the record's, not merely convert to
        # it. Measured: `PyArray_FromAny` accepts a same-itemsize structured
        # dtype whose fields are declared in the other order and hands C the
        # bytes UNCHANGED -- so `[('q','<i2'),('i','<i2')]` arrived with i
        # and q silently swapped. numpy's own equality says no to that
        # (`np.can_cast(b, a, "equiv")` is False), so the guard is exactly
        # the gh-581 rule for an `out=` buffer, one direction over: refuse
        # rather than reinterpret.
        head = (
            f"    PyArray_Descr *{descr} = {dtype_fn}_get_dtype();\n"
            f"    if (!{descr}) {{ {fail} }}\n"
            f"    if (!PyArray_Check({obj_var})\n"
            f"        || !PyArray_EquivTypes("
            f"PyArray_DESCR((PyArrayObject *){obj_var}), {descr})) {{\n"
            f"        PyErr_Format(PyExc_TypeError,\n"
            f'            "{label} must be an array of the declared record"\n'
            f'            " dtype (got %R)",\n'
            f"            PyArray_Check({obj_var})\n"
            f"                ? (PyObject *)PyArray_DESCR("
            f"(PyArrayObject *){obj_var})\n"
            f"                : (PyObject *)Py_TYPE({obj_var}));\n"
            f"        Py_DECREF({descr});\n"
            f"        {fail}\n"
            f"    }}\n"
        )
        if not strict:
            return head + (
                f"    PyArrayObject *{arr_var} ="
                f" (PyArrayObject *)PyArray_FromAny(\n"
                f"        {obj_var}, {descr}, 0, 0, {flags}, NULL);\n"
                f"    if (!{arr_var}) {{ {fail} }}\n"
            )
        # `strict` is ONE rule, so the record path tightens alongside the
        # scalar one. Its dtype was already exact; its SHAPE was not --
        # `PyArray_FromAny` would still copy a 2-D or strided input flat,
        # which is the half of the defect that is about the copy rather
        # than the type.
        return (
            head
            + _strict_rank_and_contiguity(
                obj_var,
                label,
                fail,
                extra_cleanup=f"        Py_DECREF({descr});\n",
            )
            + f"    Py_DECREF({descr});\n"
            + f"    Py_INCREF({obj_var});\n"
            + f"    PyArrayObject *{arr_var} = (PyArrayObject *){obj_var};\n"
        )
    if strict:
        return _strict_acq(
            npy_enum=npy_enum,
            expect=expect,
            obj_var=obj_var,
            arr_var=arr_var,
            fail=fail,
        )
    return (
        f"    PyArrayObject *{arr_var} =\n"
        f"        {array_arg(obj_var, npy_enum, flags, label or label_of(arr_var), hint)};\n"
        f"    if (!{arr_var}) {{ {fail} }}\n"
    )


def label_of(arr_var: str) -> str:
    """The parameter name a ``<name>_arr`` local holds, for a message."""
    return arr_var.removesuffix("_arr")


def _strict_rank_and_contiguity(
    obj_var: str, label: str, fail: str, extra_cleanup: str = ""
) -> str:
    """Refuse a non-1-D or non-contiguous input, rather than copying it.

    The half that is the same for a scalar element and a record one, so it
    is written once. ``ValueError`` and not ``TypeError``: the *type* was
    right and the SHAPE was not, which is the distinction a caller acts on.
    """
    return (
        f"    if (PyArray_NDIM((PyArrayObject *){obj_var}) != 1\n"
        f"        || !PyArray_IS_C_CONTIGUOUS("
        f"(PyArrayObject *){obj_var})) {{\n"
        f"        PyErr_Format(PyExc_ValueError,\n"
        f'            "{label} must be a 1-D C-contiguous array"\n'
        f'            " (got %d-D%s)",\n'
        f"            PyArray_NDIM((PyArrayObject *){obj_var}),\n"
        f"            PyArray_IS_C_CONTIGUOUS((PyArrayObject *){obj_var})\n"
        f'                ? "" : ", not contiguous");\n'
        f"{extra_cleanup}"
        f"        {fail}\n"
        f"    }}\n"
    )


def _strict_acq(
    *, npy_enum: str, expect: str, obj_var: str, arr_var: str, fail: str
) -> str:
    """Acquire a scalar-element input array WITHOUT converting it (gh-1426 B).

    The asymmetry this closes: jm already refuses rather than reinterprets on
    the **output** side (`out_buffer_guard`, gh-581) and on the **record**
    input side (the ``dtype_fn`` branch above, whose docstring gives the
    argument). Only a scalar-element input was still handed to
    ``PyArray_FROM_OTF``, which casts, copies and **flattens**, silently.

    For a DSP ``execute()`` that is a kindness. For a ring buffer it is two
    defects at once: a hidden allocation and copy per ``write`` on the one
    path whose entire purpose is to avoid copies, and a ``(4, 2)`` array
    accepted as 8 samples. So it is opt-in per method (``strict``) rather
    than a behaviour change under everyone.

    Takes a **new reference** to the caller's own array, exactly as
    ``PyArray_FROM_OTF`` does on success -- every call site already
    ``Py_DECREF``s what this returns, and a borrowed reference here would
    turn that into a use-after-free on the caller's object.

    Examples
    --------
    >>> c = _strict_acq(npy_enum="NPY_COMPLEX64", expect="complex64",
    ...                 obj_var="in_obj", arr_var="in_arr",
    ...                 fail="return NULL;")
    >>> "PyArray_FROM_OTF" in c          # nothing is converted
    False
    >>> "Py_INCREF(in_obj)" in c         # ...but a reference is still owned
    True
    >>> "complex64" in c                 # the refusal names what it wanted
    True
    """
    label = arr_var.removesuffix("_arr")
    return (
        f"    if (!PyArray_Check({obj_var})\n"
        f"        || PyArray_TYPE((PyArrayObject *){obj_var})"
        f" != {npy_enum}) {{\n"
        f"        PyErr_Format(PyExc_TypeError,\n"
        f'            "{label} must be an ndarray of dtype {expect}"\n'
        f'            " (got %R)",\n'
        f"        PyArray_Check({obj_var})\n"
        f"                ? (PyObject *)PyArray_DESCR("
        f"(PyArrayObject *){obj_var})\n"
        f"                : (PyObject *)Py_TYPE({obj_var}));\n"
        f"        {fail}\n"
        f"    }}\n"
        + _strict_rank_and_contiguity(obj_var, label, fail)
        + f"    Py_INCREF({obj_var});\n"
        f"    PyArrayObject *{arr_var} = (PyArrayObject *){obj_var};\n"
    )


def array_len_c(pname: str, arr_var: str, elements_per_sample: int = 1) -> str:
    """The ``size_t <p>_len`` line, in the unit the C kernel counts (gh-805 §C).

    ``PyArray_SIZE`` counts **elements**. A kernel taking an interleaved buffer
    counts **logical samples** — a complex pair in an ``int16_t[]`` is two
    elements and one sample — and jm had no way to say so, so every length
    crossing the boundary was out by the interleave factor.

    That mismatch is the dangerous kind because it compiles. `pass_capacity`
    on such a method hands the kernel an element count where it expects a pair
    count, and the kernel then writes twice as far as the caller's buffer
    allows: not a wrong answer, an overrun.

    ``elements_per_sample`` defaults to 1, so every existing param renders the
    line it always did.

    Examples
    --------
    >>> array_len_c("x", "x_arr")
    '    size_t x_len = (size_t)PyArray_SIZE(x_arr);'
    >>> array_len_c("x", "x_arr", 2)
    '    size_t x_len = (size_t)PyArray_SIZE(x_arr) / 2;'
    """
    divisor = f" / {elements_per_sample}" if elements_per_sample != 1 else ""
    return (
        f"    size_t {pname}_len = (size_t)PyArray_SIZE({arr_var}){divisor};"
    )


def output_size_c(
    var: str,
    size_expr: str,
    who: str,
    release: str = "",
    *,
    ctype: str = "npy_intp",
    limit: str = "NPY_MAX_INTP",
    indent: str = "    ",
) -> str:
    """Declare ``<ctype> <var>`` from an output SIZE, refusing one too large.

    Every binding that sizes an output from a C expression -- a function's
    ``out_size``, a method's ``max_out``, a handle's ``out_len_fn``, a
    borrowed view's count (``_context._parse.borrow_view_c``), or a length
    the caller passed as an unsigned int -- cast that value straight to
    ``npy_intp``. A ``size_t`` past ``NPY_MAX_INTP`` wraps negative there, so
    the caller saw numpy's ``ValueError: negative dimensions are not
    allowed``, which names neither the call nor the size (gh-1710). CPython
    raises ``OverflowError`` for the same condition (``bytearray(2**63)``),
    so this does too. Two sites were worse than a confusing message: the
    ``str`` output allocates ``malloc(_cap + 1)``, which wraps to
    ``malloc(0)`` for ``SIZE_MAX``, and a function's list-of-records buffer
    is ``malloc(_max * sizeof(T))``, whose product wraps to a SHORT buffer
    for a capacity the callee then fills. Both hand the callee a buffer to
    overrun, and both are bounded by *limit* before the arithmetic.

    The value is evaluated ONCE into a ``size_t`` (``<var>_need``), compared
    against *limit*, and only then converted. A negative signed expression
    becomes a huge ``size_t`` and is refused the same way, which is the
    right answer: there is no negative length to allocate.

    One emitter for every such site, because this is the pair that drifts: a
    guard written at one allocation and not its peers is exactly how the
    ndarray output would be fixed while the ``str`` output kept wrapping.

    Parameters
    ----------
    var : str
        The C local to declare, e.g. ``_dim``.
    size_expr : str
        The verbatim C expression giving the element count. It is evaluated
        exactly once.
    who : str
        The Python-facing name the ``OverflowError`` message leads with.
    release : str
        C statements that drop what the binding already owns (parsed input
        arrays), run before the ``return NULL``. Empty when nothing is held.
    ctype : str
        The type of *var*: ``npy_intp`` for a numpy dimension, ``size_t`` for
        a byte count that must still fit a ``Py_ssize_t``.
    limit : str
        The largest value *var* may hold, as a C expression. A byte count
        that is multiplied before allocating passes the quotient, e.g.
        ``(PY_SSIZE_T_MAX / sizeof(T))``, so the product cannot wrap.
    indent : str
        Leading whitespace for each emitted line.

    Examples
    --------
    >>> print(output_size_c("_dim", "n * 2", "fb", "Py_DECREF(x_arr);"))
        size_t _dim_need = (size_t)(n * 2);
        if (_dim_need > (size_t)NPY_MAX_INTP) {
            Py_DECREF(x_arr);
            PyErr_Format(PyExc_OverflowError,
                "fb: output of %zu elements is too large", _dim_need);
            return NULL;
        }
        npy_intp _dim = (npy_intp)_dim_need;
    <BLANKLINE>
    """
    i = indent
    need = f"{var}_need"
    rel = f"{i}    {release.strip()}\n" if release.strip() else ""
    return (
        f"{i}size_t {need} = (size_t)({size_expr});\n"
        f"{i}if ({need} > (size_t){limit}) {{\n"
        f"{rel}"
        f"{i}    PyErr_Format(PyExc_OverflowError,\n"
        f'{i}        "{who}: output of %zu elements is too large", {need});\n'
        f"{i}    return NULL;\n"
        f"{i}}}\n"
        f"{i}{ctype} {var} = ({ctype}){need};\n"
    )


def returned_count_c(
    count: str,
    cap: str,
    who: str,
    release: str = "",
    *,
    indent: str = "    ",
) -> str:
    """Refuse a COUNT the kernel returned that exceeds the buffer it was given.

    The read-side twin of :func:`output_size_c`. A self-sizing output hands
    the kernel a buffer of *cap* elements and then trusts the count it
    returns: the count becomes the array's dimension
    (``PyArray_DIMS(arr)[0] = n``), the ``PyArray_Resize`` target, the
    length of a ``list`` built from a records buffer, the length of a
    ``str``/``bytes`` copied out of a byte buffer, or the stop of a
    ``out[:n]`` view. A count past *cap* -- a kernel bug, or a sentinel such
    as ``(size_t)-1`` -- made each of those read past the allocation: an
    ndarray whose shape ran off its own buffer, a view over the caller's
    ``out=`` reaching past its end, a ``PyArray_Resize`` that GREW the
    result into uninitialised memory (gh-1716).

    It refuses rather than clamps. A count past the capacity means the
    kernel has very likely already WRITTEN past the end, so a trimmed result
    would hand back plausible data from a call that corrupted memory. The
    ``str`` output used to clamp the count down to ``_cap``, silently; it
    goes through here now, like every other site.

    The exception is ``RuntimeError``, the class jm already raises when the
    callee breaks its side of the contract -- ``<fn> failed (rc=%d)``,
    ``<fn> returned NULL``, ``create`` returning NULL -- as opposed to
    ``ValueError`` for an argument the CALLER got wrong. Nothing the caller
    passed can produce this; the C code did.

    The guard declares nothing: *count* and *cap* are locals the site
    already holds, so a site's set of C locals (which a manifest name may
    collide with, ``_builtins``) is unchanged. Both are converted to
    ``size_t`` before comparing, so a signed capacity (a ``Py_ssize_t``
    count the binding already proved non-negative, an ``npy_intp``) compares
    as an unsigned length.

    One emitter for every site, for the reason :func:`output_size_c` is
    one: a guard pasted per site is how the ndarray output is fixed while
    the ``str`` output keeps clamping. ``RETURNED_COUNT_GUARD_RE`` finds the
    guard this emits, for ``_docsync``'s advisory and for the tests that
    refuse a count consumed without it.

    Parameters
    ----------
    count : str
        The C local holding the count the kernel returned. An identifier,
        evaluated (twice) after the call.
    cap : str
        A C expression for the capacity, in the same unit as *count*
        (elements, records or bytes). Must still be valid after *release*
        runs, so pass a local, not ``PyArray_SIZE(<released array>)``.
    who : str
        The Python-facing name the ``RuntimeError`` message leads with.
    release : str
        C statements that drop what the binding owns at this point (the
        output array, a malloc'd buffer), run before ``return NULL``.
    indent : str
        Leading whitespace for each emitted line.

    Examples
    --------
    >>> print(returned_count_c("_n", "_dim", "fb", "Py_DECREF(_out);"))
        if ((size_t)(_n) > (size_t)(_dim)) {
            Py_DECREF(_out);
            PyErr_Format(PyExc_RuntimeError,
                "fb: wrote %zu elements into a buffer of %zu",
                (size_t)(_n), (size_t)(_dim));
            return NULL;
        }
    <BLANKLINE>
    >>> bool(RETURNED_COUNT_GUARD_RE.search(
    ...     returned_count_c("n_out", "_cap", "F.g")))
    True
    """
    i = indent
    rel = f"{i}    {release.strip()}\n" if release.strip() else ""
    return (
        f"{i}if ((size_t)({count}) > (size_t)({cap})) {{\n"
        f"{rel}"
        f"{i}    PyErr_Format(PyExc_RuntimeError,\n"
        f'{i}        "{who}: wrote %zu elements into a buffer of %zu",\n'
        f"{i}        (size_t)({count}), (size_t)({cap}));\n"
        f"{i}    return NULL;\n"
        f"{i}}}\n"
    )


#: The opening of the guard :func:`returned_count_c` emits, group 1 the count
#: it bounds. Whitespace-tolerant everywhere a C formatter may move it
#: (``(size_t) (n)`` in GNU style), and anchored on CODE, not on the message:
#: ``_docsync`` reads it from a comment- and string-masked body, where the
#: message text is blanked.
RETURNED_COUNT_GUARD_RE = re.compile(
    r"\bif\s*\(\s*\(\s*size_t\s*\)\s*\(\s*([A-Za-z_]\w*)\s*\)\s*>"
    r"\s*\(\s*size_t\s*\)\s*\("
)

#: The whole guard block, opener through its closing brace -- what
#: ``_docsync`` removes before asking whether a wrapper raises, because the
#: raise is jm's guard and not a result shape the manifest declared. The
#: body holds only statements (release, raise, return), never a brace.
RETURNED_COUNT_BLOCK_RE = re.compile(
    RETURNED_COUNT_GUARD_RE.pattern + r"[^{}]*\{[^{}]*\}"
)
