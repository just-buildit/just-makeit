"""
_types.py — C type system for just-makeit.

Defines _CTYPE_META (the canonical type registry), derived lookup tables,
and all type-query helper functions used throughout the code generator.
"""

from __future__ import annotations

import re as _re


# Shared to_py lambdas reused across fixed-width integer groups.
def _TO_PY_LONG(v):
    return f"PyLong_FromLong((long){v})"


def _TO_PY_LLONG(v):
    return f"PyLong_FromLongLong((long long){v})"


def _TO_PY_ULONG(v):
    return f"PyLong_FromUnsignedLong((unsigned long){v})"


def _TO_PY_ULLONG(v):
    return f"PyLong_FromUnsignedLongLong((unsigned long long){v})"


def _fwint(ctype, fmt, parse_type, parse_zero, np_type, to_py, zero="0"):
    """Build a fixed-width integer _CTYPE_META entry."""
    return {
        "kind": "int",
        "fmt": fmt,
        "zero": zero,
        "py_zero": "0",
        "py_type": np_type,
        "parse_type": parse_type,
        "parse_zero": parse_zero,
        "to_c": lambda n, t=ctype: f"({t}){n}_raw",
        "to_py": to_py,
    }


_CTYPE_META: dict[str, dict] = {
    # ── Floating point ────────────────────────────────────────────────────────
    "double": {
        "kind": "float",
        "fmt": "d",
        "zero": "0.0",
        "py_zero": "0.0",
        "py_type": "np.float64",
        "to_py": lambda v: f"PyFloat_FromDouble({v})",
    },
    "float": {
        "kind": "float",
        "fmt": "f",
        "zero": "0.0f",
        "py_zero": "0.0",
        "py_type": "np.float32",
        "to_py": lambda v: f"PyFloat_FromDouble((double){v})",
    },
    # ── Integers ──────────────────────────────────────────────────────────────
    "int": {
        "kind": "int",
        "fmt": "i",
        "zero": "0",
        "py_zero": "0",
        "py_type": "np.int32",
        "to_py": _TO_PY_LONG,
    },
    "bool": {
        "kind": "int",
        "fmt": "p",
        "zero": "0",
        "py_zero": "False",
        "py_type": "np.bool_",
        "parse_type": "int",
        "parse_zero": "0",
        "to_c": lambda n: f"(int){n}_raw",
        "to_py": lambda v: f"PyBool_FromLong((long)({v}))",
    },
    # Fixed-width signed
    "int8_t": _fwint("int8_t", "i", "int", "0", "np.int8", _TO_PY_LONG),
    "int16_t": _fwint("int16_t", "i", "int", "0", "np.int16", _TO_PY_LONG),
    "int32_t": _fwint("int32_t", "l", "long", "0L", "np.int32", _TO_PY_LONG),
    "int64_t": _fwint(
        "int64_t", "L", "long long", "0LL", "np.int64", _TO_PY_LLONG
    ),
    # Fixed-width unsigned
    "uint8_t": _fwint(
        "uint8_t", "I", "unsigned int", "0U", "np.uint8", _TO_PY_ULONG, "0U"
    ),
    "uint16_t": _fwint(
        "uint16_t", "I", "unsigned int", "0U", "np.uint16", _TO_PY_ULONG, "0U"
    ),
    "uint32_t": _fwint(
        "uint32_t",
        "k",
        "unsigned long",
        "0UL",
        "np.uint32",
        _TO_PY_ULONG,
        "0U",
    ),
    "uint64_t": _fwint(
        "uint64_t",
        "K",
        "unsigned long long",
        "0ULL",
        "np.uint64",
        _TO_PY_ULLONG,
        "0U",
    ),
    "size_t": _fwint(
        "size_t",
        "K",
        "unsigned long long",
        "0ULL",
        "np.uintp",
        _TO_PY_ULLONG,
        "0",
    ),
    "ptrdiff_t": _fwint(
        "ptrdiff_t", "L", "long long", "0LL", "np.intp", _TO_PY_LLONG, "0"
    ),
    # ── C99 complex — parsed via Py_complex (format "D"), then cast. ──────────
    "float _Complex": {
        "kind": "complex",
        "fmt": "D",
        "zero": "0.0f + 0.0f * I",
        "py_zero": "0j",
        "py_type": "np.complex64",
        "parse_type": "Py_complex",
        "parse_zero": "{0.0, 0.0}",
        "to_c": lambda n: f"(float){n}_raw.real + (float){n}_raw.imag * I",
        "to_py": lambda v: (
            f"PyComplex_FromDoubles((double)crealf({v}), (double)cimagf({v}))"
        ),
    },
    "double _Complex": {
        "kind": "complex",
        "fmt": "D",
        "zero": "0.0 + 0.0 * I",
        "py_zero": "0j",
        "py_type": "np.complex128",
        "parse_type": "Py_complex",
        "parse_zero": "{0.0, 0.0}",
        "to_c": lambda n: f"{n}_raw.real + {n}_raw.imag * I",
        "to_py": lambda v: f"PyComplex_FromDoubles(creal({v}), cimag({v}))",
    },
    "long double _Complex": {
        "kind": "complex",
        "fmt": "D",
        "zero": "0.0L + 0.0L * I",
        "py_zero": "0j",
        "py_type": "np.clongdouble",
        "parse_type": "Py_complex",
        "parse_zero": "{0.0, 0.0}",
        "to_c": lambda n: (
            f"(long double){n}_raw.real + (long double){n}_raw.imag * I"
        ),
        "to_py": lambda v: (
            f"PyComplex_FromDoubles((double)creall({v}), (double)cimagl({v}))"
        ),
    },
    # ── String — return-type only; step() returns a Python str. ──────────────
    "const char *": {
        "kind": "str",
        "fmt": "s",
        "zero": "NULL",
        "py_type": "str",
        "to_c": lambda n: n,
        "to_py": lambda v: f"PyUnicode_FromString({v})",
    },
}

SUPPORTED_TYPES: frozenset[str] = frozenset(_CTYPE_META)

# gh-595: natural C spellings that are NOT manifest keys, mapped to the key a
# user reaching for them almost certainly wants, and why. The width-varying
# integer spellings are deliberately absent from _CTYPE_META — a generated
# binding's PyArg format char has to match an exact width — so the hint steers
# to the fixed-width equivalent rather than registering an ambiguous type. The
# `complex` entries catch the display form (`_ctype_display` renders
# `float _Complex` as `float complex`), which reads as the obvious spelling but
# is not the key jm stores.
_WIDTH_NOTE = "has a platform-dependent width"
_SPELLING_NOTE = "is the display form; jm stores the `_Complex` spelling"
_RETURN_TYPE_HINTS: dict[str, tuple[str, str]] = {
    "char": ("int8_t", _WIDTH_NOTE),
    "short": ("int16_t", _WIDTH_NOTE),
    "long": ("int64_t", _WIDTH_NOTE),
    "long long": ("int64_t", _WIDTH_NOTE),
    "unsigned": ("uint32_t", _WIDTH_NOTE),
    "unsigned char": ("uint8_t", _WIDTH_NOTE),
    "unsigned short": ("uint16_t", _WIDTH_NOTE),
    "unsigned int": ("uint32_t", _WIDTH_NOTE),
    "unsigned long": ("uint64_t", _WIDTH_NOTE),
    "unsigned long long": ("uint64_t", _WIDTH_NOTE),
    "ssize_t": ("ptrdiff_t", _WIDTH_NOTE),
    "intptr_t": ("ptrdiff_t", _WIDTH_NOTE),
    "intp": ("ptrdiff_t", _WIDTH_NOTE),
    "str": ("const char *", "is a Python type, not a C one"),
    "char *": ("const char *", "must be const in a return position"),
    "float complex": ("float _Complex", _SPELLING_NOTE),
    "double complex": ("double _Complex", _SPELLING_NOTE),
    "long double complex": ("long double _Complex", _SPELLING_NOTE),
}


def c_param_parts(params) -> list[str]:
    """Expand a method/function param list into C parameter declarations.

    The one place that knows how a declared param becomes C. An array param
    expands to **two** C parameters — a const element pointer and a `size_t`
    length — and every generated prototype, stub signature and binding call
    has to agree on that expansion or the project will not link.

    Accepts either shape jm carries params in: ``(name, type)`` tuples (the
    CLI form) or ``{"name": ..., "type": ...}`` dicts (the manifest / apply
    replay form, which also carries `default`, `capsule`, `out`).

    Parameters
    ----------
    params : list of tuple or list of dict
        Declared params, in order.

    Returns
    -------
    list of str
        C parameter declarations, e.g.
        ``["const float complex *rx", "size_t rx_len", "size_t t0"]``.

    Examples
    --------
    >>> c_param_parts([("rx", "float _Complex[]"), ("t0", "size_t")])
    ['const float complex *rx', 'size_t rx_len', 'size_t t0']
    >>> c_param_parts([{"name": "n", "type": "int", "default": "4"}])
    ['int n']
    """
    parts: list[str] = []
    for p in params:
        pname, ptype = (p["name"], p["type"]) if isinstance(p, dict) else p[:2]
        if is_array_param_type(ptype):
            elem_disp = _ctype_display(array_elem_ctype(ptype))
            parts.append(f"const {elem_disp} *{pname}")
            parts.append(f"size_t {pname}_len")
        else:
            parts.append(f"{_ctype_display(ptype)} {pname}")
    return parts


def c_param_suppress(params) -> list[str]:
    """``(void)name;`` statements matching :func:`c_param_parts`' expansion.

    A generated stub must silence every parameter it does not yet use,
    including the synthesised ``<name>_len`` of an array param — otherwise the
    scaffold warns (or fails under ``-Werror``) the moment it is compiled.

    Parameters
    ----------
    params : list of tuple or list of dict
        Declared params, in order — the same input as ``c_param_parts``.

    Returns
    -------
    list of str
        One ``(void)x;`` per generated C parameter.

    Examples
    --------
    >>> c_param_suppress([("rx", "float[]"), ("t0", "size_t")])
    ['(void)rx;', '(void)rx_len;', '(void)t0;']
    """
    out: list[str] = []
    for p in params:
        pname, ptype = (p["name"], p["type"]) if isinstance(p, dict) else p[:2]
        out.append(f"(void){pname};")
        if is_array_param_type(ptype):
            out.append(f"(void){pname}_len;")
    return out


def is_supported_return_type(
    return_type: str, *, allow_array: bool = False
) -> bool:
    """True when ``return_type`` is ``void`` or a registered scalar.

    The single predicate behind every "is this return type real?" check —
    the two CLI front-ends (``jm function --return-type`` /
    ``jm method --return-type``) and the manifest validation that ``jm apply``
    runs. Keeping one copy matters: before gh-595 the CLI rejected an
    unregistered type while the TOML path silently generated a binding that
    discarded the C return value and handed back ``None``.

    A record shape (``result_fields``) legitimately names a user-defined
    struct here, so callers pass that exemption themselves rather than this
    predicate trying to guess the shape.

    Parameters
    ----------
    return_type : str
        The manifest/CLI return type string, e.g. ``"int64_t"``.
    allow_array : bool, optional
        Also accept an array spelling (``"T[]"``) whose element type is
        registered. The two front-ends differ here and always have: the
        ``--return-type`` flags are scalar-or-void only ("must be void or a
        scalar"), while a manifest may declare an array return on a block or
        capsule method, so the manifest walk opts in. Default False, matching
        the CLI.

    Returns
    -------
    bool
        True if a binding can convert this type back to Python.

    Examples
    --------
    >>> is_supported_return_type("void")
    True
    >>> is_supported_return_type("size_t")
    True
    >>> is_supported_return_type("long")
    False
    >>> is_supported_return_type("float _Complex[]")
    False
    >>> is_supported_return_type("float _Complex[]", allow_array=True)
    True
    >>> is_supported_return_type("long[]", allow_array=True)
    False
    """
    if return_type == "void" or return_type in _CTYPE_META:
        return True
    if allow_array and return_type.endswith("[]"):
        return return_type[:-2] in _CTYPE_META
    return False


def unsupported_return_type_help(
    return_type: str, *, allow_void: bool = True
) -> str:
    """Two-line explanation for an unsupported type.

    Renders the supported set, plus a "did you mean" line when the offending
    spelling is a known C synonym of a registered type (see
    ``_RETURN_TYPE_HINTS``).

    Parameters
    ----------
    return_type : str
        The rejected type string, used to look up a suggestion.
    allow_void : bool, optional
        Whether ``void`` belongs in the supported list. True for a return type;
        False for a ``result_fields`` entry (gh-598), where every field is a
        value the binding must convert, so ``void`` is not a candidate and
        listing it would send the reader down a dead end.

    Returns
    -------
    str
        Message body (no trailing newline), ready to append to an
        ``error: ...`` first line.

    Examples
    --------
    >>> print(unsupported_return_type_help("long"))
    Supported: void, bool, const char *, double, double _Complex, float, float _Complex, int, int16_t, int32_t, int64_t, int8_t, long double _Complex, ptrdiff_t, size_t, uint16_t, uint32_t, uint64_t, uint8_t
    Did you mean 'int64_t'? ('long' has a platform-dependent width.)
    >>> print(unsupported_return_type_help("nope", allow_void=False))
    Supported: bool, const char *, double, double _Complex, float, float _Complex, int, int16_t, int32_t, int64_t, int8_t, long double _Complex, ptrdiff_t, size_t, uint16_t, uint32_t, uint64_t, uint8_t
    """
    _prefix = "void, " if allow_void else ""
    msg = f"Supported: {_prefix}{', '.join(sorted(_CTYPE_META))}"
    hinted = _RETURN_TYPE_HINTS.get(return_type.strip())
    if hinted:
        suggestion, why = hinted
        msg += f"\nDid you mean '{suggestion}'? ('{return_type}' {why}.)"
    return msg


# Maps py_type -> NumPy C-API enum constant (for ext.c array ops).
_NP_ENUM: dict[str, str] = {
    "np.float32": "NPY_FLOAT",
    "np.float64": "NPY_DOUBLE",
    "np.complex64": "NPY_COMPLEX64",
    "np.complex128": "NPY_COMPLEX128",
    "np.clongdouble": "NPY_CLONGDOUBLE",
    "np.int8": "NPY_INT8",
    "np.int16": "NPY_INT16",
    "np.int32": "NPY_INT32",
    "np.int64": "NPY_INT64",
    "np.uint8": "NPY_UINT8",
    "np.uint16": "NPY_UINT16",
    "np.uint32": "NPY_UINT32",
    "np.uint64": "NPY_UINT64",
    "np.uintp": "NPY_UINTP",
    "np.intp": "NPY_INTP",
    # bool is a registered scalar arg/return type; without this entry
    # make_sample_ctx's `_NP_ENUM[out_np_dtype]` lookup KeyErrors on it.
    "np.bool_": "NPY_BOOL",
    # const char * — return-type only; steps() array path does not apply.
    "str": "NPY_OBJECT",
}

# Maps user-facing numpy dtype names to (C element type, NPY enum constant).
_ARRAY_DTYPE: dict[str, tuple[str, str]] = {
    "float32": ("float", "NPY_FLOAT"),
    "float64": ("double", "NPY_DOUBLE"),
    "complex64": ("float _Complex", "NPY_COMPLEX64"),
    "complex128": ("double _Complex", "NPY_COMPLEX128"),
    "int8": ("int8_t", "NPY_INT8"),
    "int16": ("int16_t", "NPY_INT16"),
    "int32": ("int32_t", "NPY_INT32"),
    "int64": ("int64_t", "NPY_INT64"),
    "uint8": ("uint8_t", "NPY_UINT8"),
    "uint16": ("uint16_t", "NPY_UINT16"),
    "uint32": ("uint32_t", "NPY_UINT32"),
    "uint64": ("uint64_t", "NPY_UINT64"),
    "uintp": ("size_t", "NPY_UINTP"),
    "intp": ("ptrdiff_t", "NPY_INTP"),
}

SUPPORTED_ARRAY_DTYPES: frozenset[str] = frozenset(_ARRAY_DTYPE)

# Reverse of _ARRAY_DTYPE: C element type (with _Complex) -> NPY enum.
_CTYPE_TO_NPY: dict[str, str] = {
    c_type: npy_enum for c_type, npy_enum in _ARRAY_DTYPE.values()
}

SUPPORTED_ARRAY_CTYPES: frozenset[str] = frozenset(_CTYPE_TO_NPY)

# C type -> canonical dtype name (reverse of the ctype column in _ARRAY_DTYPE).
_CTYPE_TO_DTYPE: dict[str, str] = {
    c_type: dtype for dtype, (c_type, _) in _ARRAY_DTYPE.items()
}

# Maps kind -> Python isinstance target.
_KIND_PY_ISINSTANCE: dict[str, str] = {
    "float": "float",
    "int": "int",
    "complex": "complex",
    "str": "str",
}

# Maps kind -> Python test input literal.
_KIND_PY_TEST_VAL: dict[str, str] = {
    "float": "1.0",
    "int": "1",
    "complex": "1.0 + 0.0j",
    "str": '"hello"',
}


def record_tuple_build(result_fields: list[dict], accessor: str) -> str:
    """``Py_BuildValue`` arguments for one ``result_fields`` record (gh-598).

    Renders the format string and argument list that turn one C record struct
    into a Python tuple, e.g.::

        "(NN)", PyLong_FromLongLong((long long)_results[_i].idx),
                PyFloat_FromDouble(_results[_i].mag)

    Every field converts through its ``_CTYPE_META["to_py"]`` — the same
    primitive the ``single = true`` record path, scalar returns and property
    getters already use — so a type is converted correctly or not at all.

    This replaced a second, smaller ``_PYBUILD_FMT`` table that mapped a field
    type to a bare format char plus an optional cast, and fell back to
    ``("i", "")`` on a miss. The fallback supplied **no cast**, so an unmapped
    field reached ``Py_BuildValue``'s varargs under an ``int`` format — an ABI
    mismatch rather than a conversion. It did not take a typo to hit: ten types
    registered in ``_CTYPE_META`` were absent from that table, so a plain
    ``ptrdiff_t`` field silently truncated (5000000000 read back as 705032704)
    while compiling without a warning. Five of the ten (``bool``, ``int8_t``,
    ``int16_t``, ``uint8_t``, ``uint16_t``) were *accidentally* correct because
    default argument promotion widens them to ``int``, which is precisely what
    made the gap survive: a test sweeping small integer types comes back green.

    The ``N`` format takes a ``PyObject *`` and **steals** the reference, which
    is the documented idiom for an object constructed in the argument list — so
    the conversions cannot leak, and a NULL from any of them propagates as a
    failed ``Py_BuildValue`` the callers already check.

    Parameters
    ----------
    result_fields : list of dict
        ``[{"name": ..., "type": ...}, ...]``; every type must be registered in
        ``_CTYPE_META`` (``jm apply`` validates this up front, so a ``KeyError``
        here means a caller bypassed that check).
    accessor : str
        C expression for the record being converted, without a trailing dot —
        e.g. ``"_results[_i]"`` or ``"results[i]"``.

    Returns
    -------
    str
        The complete ``Py_BuildValue`` argument text: a quoted format string,
        then one converted field per entry, comma-separated.

    Examples
    --------
    >>> print(record_tuple_build([{"name": "n", "type": "size_t"}], "r[i]"))
    "(N)", PyLong_FromUnsignedLongLong((unsigned long long)r[i].n)
    """
    fmt = "(" + "N" * len(result_fields) + ")"
    items = [
        _CTYPE_META[f["type"]]["to_py"](f"{accessor}.{f['name']}")
        for f in result_fields
    ]
    return ", ".join([f'"{fmt}"'] + items)


# Regex for fixed-size array state types like 'float[64]' or 'double _Complex[32]'.
_ARRAY_RE = _re.compile(r"^(.+)\[(\d+)\]$")


def normalize_array_dtype(s: str) -> str | None:
    """Return the canonical dtype name for s, accepting both dtype and C-type forms.

    Returns None if s is not a recognised dtype or C element type.
    """
    if s in _ARRAY_DTYPE:
        return s
    return _CTYPE_TO_DTYPE.get(s)


def is_array_param_type(ptype: str) -> bool:
    """Return True if ptype is an array parameter spec (ends with '[]')."""
    return ptype.endswith("[]")


def array_param_ndim(ptype: str) -> int:
    """Return the number of dimensions for an array param type (1 or 2)."""
    return 2 if ptype.endswith("[][]") else 1


def array_elem_ctype(ptype: str) -> str:
    """Strip all '[]' suffixes to get the element C type.

    Examples: 'float _Complex[]' -> 'float _Complex'
              'float _Complex[][]' -> 'float _Complex'
    """
    while ptype.endswith("[]"):
        ptype = ptype[:-2]
    return ptype


def parse_out_type(out_type: str) -> tuple[str, str | None]:
    """Parse an out_type value and return (c_element_type, len_param_or_None).

    Accepts two forms:
      - bare C type:           ``"double"``    → ``("double", None)``
      - numpy dtype + scalar:  ``"float64[M]"`` → ``("double", "M")``

    The ``[name]`` suffix names the scalar C parameter whose runtime value
    is used as the output-array length.  The dtype prefix (``float64``,
    ``uint32``, etc.) is resolved via ``_ARRAY_DTYPE``; bare C types
    (``double``, ``float``) are accepted directly via ``_CTYPE_TO_NPY``.
    """
    m = _re.fullmatch(r"(.+?)\[([A-Za-z_][A-Za-z_0-9]*)\]", out_type)
    if m:
        base, param_name = m.group(1), m.group(2)
        if base in _ARRAY_DTYPE:
            c_type = _ARRAY_DTYPE[base][0]
        elif base in _CTYPE_TO_NPY:
            c_type = base
        else:
            c_type = base
        return c_type, param_name
    return out_type, None


def _join_fmt_with_optional(fmt_chars: list[str], params: list[dict]) -> str:
    """Join PyArg format chars, inserting ``|`` before the first optional param.

    A param dict with a truthy ``default`` key is *optional* (omitting the arg
    yields the default). The ``|`` marker in a ``PyArg_ParseTuple[AndKeywords]``
    format separates required from optional, so every optional param must follow
    all required ones — the same constraint Python enforces as "a non-default
    parameter cannot follow a default one". Each param contributes exactly one
    format char (an array param is a single ``O``), so param index == fmt index.

    Raises ``ValueError`` if a required param follows a defaulted one.
    """
    first_opt = next(
        (
            i
            for i, p in enumerate(params)
            if p.get("default") not in (None, "")
        ),
        None,
    )
    if first_opt is None:
        return "".join(fmt_chars)
    for p in params[first_opt:]:
        if p.get("default") in (None, ""):
            raise ValueError(
                f"parameter '{p['name']}' has no default but follows a "
                "defaulted parameter; defaulted parameters must come last"
            )
    return (
        "".join(fmt_chars[:first_opt]) + "|" + "".join(fmt_chars[first_opt:])
    )


def is_string_enum_type(ptype: str) -> bool:
    """Return True if ptype is a string-enum spec ('string_enum:a,b,...')."""
    return ptype.startswith("string_enum:")


def string_enum_choices(ptype: str) -> list[str]:
    """Return the ordered choice list from a 'string_enum:a,b,...' type."""
    return ptype[len("string_enum:") :].split(",")


def is_enum_ref(ptype: str) -> bool:
    """Return True if ptype references a named ``[[enum]]`` ('enum:<name>').

    The single-source-of-truth form: instead of inlining the choices on every
    parameter (``string_enum:tone,noise,…``), a parameter names a top-level
    ``[[enum]]`` table once and refers to it (``enum:wfm_type``). The config
    layer resolves the reference to the equivalent ``string_enum:`` spec, so
    every downstream consumer (choice flags, stubs, C state) is unchanged.
    """
    return ptype.startswith("enum:")


def enum_ref_name(ptype: str) -> str:
    """Return the enum name from an 'enum:<name>' reference."""
    return ptype[len("enum:") :]


# gh-543: the container property kinds. A property whose `type` is one of
# these is backed by an iteration protocol the core implements (count/key/value
# accessors) rather than by a scalar C value, so it never reaches _CTYPE_META.
CONTAINER_KINDS: tuple[str, ...] = ("dict", "list", "tuple")

# The element type that means "the core hands back a PyObject * itself". The
# escape hatch for values jm cannot type statically -- e.g. a value whose
# Python type is chosen by a type code stored in the file being read.
OBJECT_VALUE_TYPE = "object"


def is_container_type(ctype: str) -> bool:
    """Return True if ctype names a container property kind (gh-543).

    >>> is_container_type("dict")
    True
    >>> is_container_type("double")
    False
    """
    return ctype in CONTAINER_KINDS


def is_valid_value_type(vtype: str) -> bool:
    """Return True if vtype is a legal container element type (gh-543).

    Either a scalar jm type -- jm emits the conversion itself -- or the
    ``object`` escape hatch, where the core returns a ``PyObject *``.

    >>> is_valid_value_type("const char *")
    True
    >>> is_valid_value_type("object")
    True
    >>> is_valid_value_type("double[]")
    False
    """
    return vtype == OBJECT_VALUE_TYPE or vtype in _CTYPE_META


def parse_array_type(ctype: str) -> tuple[str, int] | None:
    """Return (elem_ctype, size) if ctype is a valid array type, else None.

    Valid: 'float[64]', 'double _Complex[32]', 'int32_t[128]'
    The element type must be a key in _CTYPE_META.
    """
    m = _ARRAY_RE.match(ctype.strip())
    if not m:
        return None
    elem = m.group(1).rstrip()
    if elem not in _CTYPE_META:
        return None
    return (elem, int(m.group(2)))


def is_valid_type(ctype: str) -> bool:
    """Return True for scalar types in _CTYPE_META or array types like float[64]."""
    return ctype in _CTYPE_META or parse_array_type(ctype) is not None


# The Python *builtin* a scalar C value crosses back as. A scalar getter
# returns PyFloat_FromDouble / PyLong_From… — a Python float/int, NOT a numpy
# scalar — so a scalar `.pyi` annotation must be the builtin. (Arrays are a
# separate case: they cross as NDArray[np.<dtype>], using `py_type`.) Keyed on
# `kind` so every scalar of a kind maps identically.
_KIND_TO_PY: dict[str, str] = {
    "float": "float",
    "int": "int",
    "complex": "complex",
    "str": "str",
}


def scalar_py_annotation(ctype: str) -> str:
    """Python-type annotation for a SCALAR C value (gh-… stub conformance).

    The single source of truth for "what Python type does a scalar getter
    return / a scalar ctor param accept". The runtime hands back a Python
    builtin, so annotating a scalar as its numpy dtype (``np.float64``) both
    mis-describes the type and, with a plain default like ``= 1.0``, is an
    outright mypy error (``float`` is not ``float64``). Arrays keep the numpy
    dtype via ``NDArray[...]`` and do not use this.

    >>> scalar_py_annotation("double")
    'float'
    >>> scalar_py_annotation("size_t")
    'int'
    >>> scalar_py_annotation("float _Complex")
    'complex'
    """
    if ctype == "void":
        return "None"
    if ctype == "bool":
        return "bool"
    meta = _CTYPE_META.get(ctype)
    return _KIND_TO_PY.get(meta["kind"], "Any") if meta else "Any"


def _ctype_display(ct: str) -> str:
    """Internal key -> C display form: 'float _Complex' -> 'float complex'."""
    return ct.replace("_Complex", "complex")
