"""_context/_types.py — type-conversion constants and helpers.

Re-exports and extends the outer _types.py with context-local
lookups needed by the make_*_ctx() builders.
"""

from __future__ import annotations

from .._types import (
    _CTYPE_META,
    strip_c_literal_suffix,
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

#: Smoke-test set-values per ctype, as ``(C literal, Python literal)``.
#:
#: ONE table rather than two, because two is how this went wrong. gh-610
#: established that ``bool``'s ``kind`` is ``"int"`` -- there is no distinct
#: "bool" kind -- so it silently takes the integer path unless the concrete
#: ctype is special-cased, and it added that case to :func:`_py_default` and
#: :func:`just_makeit._stubs._py_default_stub`. The two sample-value helpers
#: were the peers it did not reach, and they are the ones the generated TESTS
#: read: a `bool` state field scaffolded ``set(2)`` then ``== 2``, which a C
#: bool can never satisfy, so `jm new --state "flag:bool:false"` produced a
#: project whose CTest *and* pytest failed on the first run (gh-1067).
_SET_VAL: dict[str, tuple[str, str]] = {
    "bool": ("true", "True"),
    "float": ("2.0f", "2.0"),
    "double": ("2.0", "2.0"),
    "float _Complex": ("2.0f + 0.0f * I", "1.0+0.0j"),
    "double _Complex": ("2.0 + 0.0 * I", "1.0+0.0j"),
    "long double _Complex": ("2.0L + 0.0L * I", "1.0+0.0j"),
}

#: Every integer type. The round-trip only needs a value that survives it.
_SET_VAL_DEFAULT = ("2", "2")


def _c_set_val(ctype: str) -> str:
    """Return a C literal suitable for setter smoke tests."""
    return _SET_VAL.get(ctype, _SET_VAL_DEFAULT)[0]


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
    if ctype == "bool":
        # gh-610: bool's `kind` is "int" (there is no distinct "bool" kind),
        # so this must dispatch on the concrete ctype — otherwise a bool
        # default falls through to the generic branch below, which passes
        # the C/TOML spelling `true`/`false` straight into generated Python,
        # a NameError (`true` is not a Python name).
        if not default.strip():
            return "..."
        return "True" if default.strip().lower() == "true" else "False"
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
    # gh-1043: the integer bucket. `0U` is a C literal and a SyntaxError in
    # Python, and this function ALREADY knew C literals carry suffixes — the
    # float branch two above strips `fF`. The knowledge was in the function
    # and simply had not been applied to the other kind.
    return strip_c_literal_suffix(default) if default.strip() else "..."


def _py_sample_val(meta: dict, ctype: str = "") -> str:
    """Return a Python test set-value for the given type metadata.

    *ctype* is optional only for callers that genuinely have no concrete type
    to hand; pass it whenever it is available. ``bool`` cannot be recognised
    from *meta* alone -- its ``kind`` is ``"int"`` -- and that is exactly the
    case gh-1067 was about.
    """
    if ctype:
        return _SET_VAL.get(ctype, _SET_VAL_DEFAULT)[1]
    if meta["kind"] == "complex":
        return "1.0+0.0j"
    if meta["kind"] == "float":
        return "2.0"
    return "2"
