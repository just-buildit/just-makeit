"""_context/_types.py — type-conversion constants and helpers.

Re-exports and extends the outer _types.py with context-local
lookups needed by the make_*_ctx() builders.
"""

from __future__ import annotations

from .._types import (
    _CTYPE_META,
)

# Maps scalar element type → NumPy C-API enum (for fixed-size array state).
_NP_DTYPE_ENUM: dict[str, str] = {
    "float": "NPY_FLOAT",
    "double": "NPY_DOUBLE",
    "int": "NPY_INT",
    "int8_t": "NPY_INT8",
    "int16_t": "NPY_INT16",
    "int32_t": "NPY_INT32",
    "int64_t": "NPY_INT64",
    "uint8_t": "NPY_UINT8",
    "uint16_t": "NPY_UINT16",
    "uint32_t": "NPY_UINT32",
    "uint64_t": "NPY_UINT64",
    "float _Complex": "NPY_COMPLEX64",
    "double _Complex": "NPY_COMPLEX128",
    "long double _Complex": "NPY_CLONGDOUBLE",
}

# Literal values used in C setter smoke tests.
_C_SET_VAL: dict[str, str] = {
    "float": "2.0f",
    "double": "2.0",
    "float _Complex": "2.0f + 0.0f * I",
    "double _Complex": "2.0 + 0.0 * I",
    "long double _Complex": "2.0L + 0.0L * I",
}


def _c_set_val(ctype: str) -> str:
    """Return a C literal suitable for setter smoke tests."""
    return _C_SET_VAL.get(ctype, "2")  # all integer types → "2"


def _py_default(ctype: str, default: str) -> str:
    """Convert a C default literal to a valid Python literal.

    When a branch cannot form a literal — the ``str`` and integer paths given
    an absent default — the result is the ``...`` sentinel (gh-515) rather than
    the empty string. ``...`` is the idiomatic stub placeholder and the same
    marker :func:`just_makeit._stubs._py_default_stub` emits, so the two stub
    paths agree; the empty string emitted ``path: str = `` into the .pyi, a
    SyntaxError that broke the entire stub. Callers must treat ``...`` as "not
    constructible" and suppress any generated construction example.

    The float and complex branches already synthesise a valid zero literal that
    mirrors the C side's zero-seed, so they are left untouched and their output
    stays byte-identical.
    """
    kind = _CTYPE_META[ctype]["kind"]
    if kind == "float":
        s = default.rstrip("fF")
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    if kind == "complex":
        return "0j"
    if kind == "str":
        # `const char *` defaults: C "NULL" becomes an empty Python
        # string literal. None would fit the semantics better, but
        # the generated CPython binding uses the "s" format code
        # which rejects None — empty string `""` passes the type
        # check, doesn't crash, and gives the user a clear placeholder
        # to swap for a real fixture path in their tests.
        # Any other C string literal (e.g. "/dev/null") is already
        # quoted in the TOML default and passes through verbatim.
        if default == "NULL":
            return '""'
        return default if default.strip() else "..."
    return default if default.strip() else "..."


def _py_sample_val(meta: dict) -> str:
    """Return a Python test set-value for the given type metadata."""
    if meta["kind"] == "complex":
        return "1.0+0.0j"
    if meta["kind"] == "float":
        return "2.0"
    return "2"
