"""
_types.py — C type system for just-makeit.

Defines _CTYPE_META (the canonical type registry), derived lookup tables,
and all type-query helper functions used throughout the code generator.
"""

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

# Py_BuildValue format char + C cast type (without parentheses).
# Applied as f"({cast}){expr}" — empty string means no cast needed.
_PYBUILD_FMT: dict[str, tuple[str, str]] = {
    "float": ("f", ""),
    "double": ("d", ""),
    "int": ("i", ""),
    "int32_t": ("i", "int"),
    "uint32_t": ("I", "unsigned int"),
    "int64_t": ("L", "long long"),
    "uint64_t": ("K", "unsigned long long"),
    "size_t": ("K", "unsigned long long"),
    "unsigned int": ("I", "unsigned int"),
    "unsigned long": ("k", "unsigned long"),
}

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


def is_string_enum_type(ptype: str) -> bool:
    """Return True if ptype is a string-enum spec ('string_enum:a,b,...')."""
    return ptype.startswith("string_enum:")


def string_enum_choices(ptype: str) -> list[str]:
    """Return the ordered choice list from a 'string_enum:a,b,...' type."""
    return ptype[len("string_enum:") :].split(",")


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


def _ctype_display(ct: str) -> str:
    """Internal key -> C display form: 'float _Complex' -> 'float complex'."""
    return ct.replace("_Complex", "complex")
