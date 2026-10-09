"""_context/_types.py — type-conversion constants and helpers.

Re-exports and extends the outer _types.py with context-local
lookups needed by the make_*_ctx() builders.
"""

from __future__ import annotations

from .._report import Refusal
from .._types import (
    _CTYPE_META,
    STATE_ARRAY_NPY,
    bool_default_py,
    field_default_py,
    is_c_only_default,
    literal_default_error,
    string_default_literal,
    strip_c_literal_suffix,
)

# Maps scalar element type → NumPy C-API enum (for fixed-size array state).
# The table lives in _types, beside the array-parameter one it extends
# (gh-1514: this was a hand-kept copy that drifted from it).
_NP_DTYPE_ENUM: dict[str, str] = STATE_ARRAY_NPY

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
    "float _Complex": ("2.0f + 0.0f * I", "1.0 + 0.0j"),
    "double _Complex": ("2.0 + 0.0 * I", "1.0 + 0.0j"),
    "long double _Complex": ("2.0L + 0.0L * I", "1.0 + 0.0j"),
}

#: Every integer type. The round-trip only needs a value that survives it.
_SET_VAL_DEFAULT = ("2", "2")


def _c_set_val(ctype: str) -> str:
    """Return a C literal suitable for setter smoke tests."""
    return _SET_VAL.get(ctype, _SET_VAL_DEFAULT)[0]


#: The kinds whose field may hold a declared default only approximately: a
#: decimal literal rounds to the field's precision when it is assigned
#: (gh-1883). Every generated test that restates a default asks this one
#: question, the C face and the Python face alike.
_ROUNDING_KINDS = ("float", "complex")


def _c_held_default(ctype: str, default: str) -> str:
    """The value a *ctype* field holds once *default* is assigned, as C.

    gh-1883. ``--state a:float:0.1`` scaffolds ``state->a = 0.1;``, which
    stores ``(float)0.1`` -- and the generated C test then asserted
    ``get_a(obj) == 0.1``, comparing that float against the DOUBLE ``0.1``.
    The two differ for every value a float cannot represent, so the scaffold
    failed its own test on the first ``make test``: gh-1067's shape, an
    assertion that cannot hold, for a float rather than a bool.

    The cast is the conversion the create and reset assignments apply, so the
    check stays exact -- it asserts precisely what jm stored, with no
    tolerance to choose. It is a no-op where the literal already has the
    field's type (``0.1`` into a ``double``, ``0.1f`` into a ``float``), and
    it covers every spelling that does not: a ``float _Complex`` given
    ``0.1 + 0.2 * I``, a ``double`` given ``0.1L``, a header constant such as
    ``M_PI``. Integer kinds are returned unchanged; a default an integer
    field cannot hold is out of range, not rounded.

    Examples
    --------
    >>> _c_held_default("float", "0.1")
    '(float)(0.1)'
    >>> _c_held_default("float _Complex", "0.1 + 0.2 * I")
    '(float _Complex)(0.1 + 0.2 * I)'
    >>> _c_held_default("double", "M_PI")
    '(double)(M_PI)'
    >>> _c_held_default("uint8_t", "3"), _c_held_default("bool", "true")
    ('3', 'true')
    """
    if _CTYPE_META[ctype]["kind"] in _ROUNDING_KINDS:
        return f"({ctype})({default})"
    return default


def _py_default(ctype: str, default: str) -> str:
    """Convert a C default literal to a valid Python literal.

    gh-1946: THE Python spelling of a declared default. A default is one
    value with two spellings; the C faces write the manifest's, and every
    Python face -- the generated test, the stub's signature, its Parameters
    entry and construction doctest, the runtime docstring, a method or
    function parameter -- asks this function, the module stub generator
    through :func:`just_makeit._stubs._py_default_stub`. A numeric literal
    Python has no spelling of for *ctype* (``08``; ``1.5`` into an ``int``)
    is refused here, :func:`~just_makeit._types.literal_default_error`'s
    answer, rather than rendered into Python that fails.

    When a branch cannot form a literal — the ``str`` and integer paths given
    an absent default — the result is the ``...`` sentinel (gh-515) rather than
    the empty string. ``...`` is the idiomatic stub placeholder and the same
    marker :func:`just_makeit._stubs._py_default_stub` emits, so the two stub
    paths agree; the empty string emitted ``path: str = `` into the .pyi, a
    SyntaxError that broke the entire stub. Callers must treat ``...`` as "not
    constructible" and suppress any generated construction example.

    Given no default, the float and complex branches synthesise the zero
    literal that mirrors the C side's zero-seed (``.0``, ``0j``).

    Examples
    --------
    >>> _py_default("double", "0.1L"), _py_default("float", "2")
    ('0.1', '2.0')
    >>> _py_default("uint64_t", "10ULL"), _py_default("int", "017")
    ('10', '0o17')
    >>> _py_default("int", "1.5")  # doctest: +ELLIPSIS
    Traceback (most recent call last):
      ...
    just_makeit._report.Refusal: default `1.5` is not an integer, ...
    """
    why = literal_default_error(ctype, default)
    if why:
        raise Refusal(why)
    kind = _CTYPE_META[ctype]["kind"]
    if ctype == "bool":
        # gh-610: bool's `kind` is "int" (there is no distinct "bool" kind),
        # so this must dispatch on the concrete ctype — otherwise a bool
        # default falls through to the generic branch below, which passes
        # the C/TOML spelling `true`/`false` straight into generated Python,
        # a NameError (`true` is not a Python name).
        if not default.strip() or is_c_only_default(ctype, default):
            # gh-1506: `FLAG_ON` is C, like gh-1488's `LVL_INFO`.
            return "..."
        return bool_default_py(default)
    if is_c_only_default(ctype, default):
        # gh-1488: a header constant (`LVL_INFO`, `M_PI`) is C, and Python
        # has no name for it -- the float branch below used to make it
        # `M_PI.0`, a SyntaxError. `...` is the answer `default_raw` already
        # gives for the same value, and every caller reads it as "no literal".
        return "..."
    if kind in ("float", "complex") and default.strip():
        # gh-1946: through the one rule for a value that rounds. Stripping
        # only `fF` here sent `0.1L` into Python verbatim, a SyntaxError on
        # every face. gh-1561: a complex default is the declared value, not
        # always zero.
        return field_default_py(ctype, default)
    if kind == "float":
        # No default is the zero-seed, as the C side seeds it.
        return ".0"
    if kind == "complex":
        return "0j"
    if kind == "str":
        # gh-1271: `NULL` is `None`, through the shared answer. It used to be
        # `""` here, and the comment said exactly why: *"None would fit the
        # semantics better, but the generated CPython binding uses the `s`
        # format code which rejects None."* `_types.param_fmt` emits `z` for
        # this parameter now, so the constraint is gone and the workaround
        # goes with it -- `""` was a different value from the declared one,
        # and the generated doctest constructed the object with it.
        return string_default_literal(default)
    # gh-1043: the integer bucket. `0U` is a C literal and a SyntaxError in
    # Python, and this function ALREADY knew C literals carry suffixes — the
    # float branch stripped `fF`. The knowledge was in the function and
    # simply had not been applied to the other kind. gh-1946: one helper
    # reads every suffix for both, and spells an octal `010` as `0o10`.
    return strip_c_literal_suffix(default) if default.strip() else "..."


def _py_eq(lhs: str, rhs: str) -> str:
    """A generated test's equality assertion, spelled the way ruff accepts.

    ``x == True`` is ruff's ``E712`` (gh-1478): a ``bool`` state field's
    default and sample value render as the singletons, which compare by
    identity.

    >>> _py_eq("obj.get_on()", "True")
    'obj.get_on() is True'
    >>> _py_eq("obj.get_k()", "3")
    'obj.get_k() == 3'
    """
    op = "is" if rhs in ("True", "False", "None") else "=="
    return f"{lhs} {op} {rhs}"


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
        return "1.0 + 0.0j"
    if meta["kind"] == "float":
        return "2.0"
    return "2"
