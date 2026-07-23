"""Declarative variant codecs — the SSOT for discriminant-tagged binary values.

A *codec* maps a runtime **discriminant** value (a small tag, e.g. a ``char``
type code) to a C **element type**, so a single value can be encoded/decoded as
any of a fixed set of C widths chosen at call time. It is the manifest primitive
behind zero-hand-binding read/write of typed-binary tag systems (BLUE/SigMF
keywords, HDF5-style attributes): the *same* declared table drives both the
input pack (Python -> bytes) and the output decode (bytes -> Python), so the two
directions cannot drift.

Declared once at the top level, keyed by name (like ``[module.X]``):

.. code-block:: toml

    [codec.blue_keyword]
    discriminant = "char"       # C type of the tag that selects a branch
    scalar_collapse = true       # decode: count==1 -> a scalar, else a list
    entries = [
      { code = "A", ctype = "char",    bytes = true },  # raw bytes -> str
      { code = "B", ctype = "int8_t"  },                # -> int
      { code = "I", ctype = "int16_t" },
      { code = "L", ctype = "int32_t" },
      { code = "X", ctype = "int64_t" },
      { code = "F", ctype = "float"   },                # -> float
      { code = "D", ctype = "double"  },
    ]

Each numeric ``ctype`` must be a scalar in :data:`_types._CTYPE_META`; the
Python type an entry crosses as is *derived* from the ctype (``int``/``float``
via :func:`_types.scalar_py_annotation`), except a ``bytes = true`` entry which
is packed raw and decoded as ``str`` — so an entry declares only ``code`` +
``ctype`` (+ optional ``bytes``), never a redundant ``py`` that could drift from
the ctype.

A method refers to a codec with ``codec = "blue_keyword"`` (packs a variant
argument on input); a container property refers to one with the same key
(decodes on output).
"""

from __future__ import annotations

from . import _types as T


class CodecError(ValueError):
    """A malformed ``[codec.X]`` declaration."""


def codecs(cfg: dict) -> dict[str, dict]:
    """Return the project's named codecs, ``{name: {discriminant, entries…}}``."""
    return cfg.get("codec", {}) or {}


def codec(cfg: dict, name: str) -> dict | None:
    """Return the named codec's declaration, or ``None`` if undeclared."""
    return codecs(cfg).get(name)


def codec_entries(cdc: dict) -> list[dict]:
    """Return a codec's ordered ``entries`` list (empty if absent)."""
    return list(cdc.get("entries", []))


def discriminant_ctype(cdc: dict) -> str:
    """The C type of the tag that selects a branch (default ``char``)."""
    return cdc.get("discriminant", "char")


def entry_is_bytes(entry: dict) -> bool:
    """True for the raw-bytes / string branch (packed raw, decoded ``str``)."""
    return bool(entry.get("bytes"))


def entry_py(entry: dict) -> str:
    """The Python builtin one codec element crosses as (``str``/``int``/``float``).

    Derived from the entry's ``ctype`` (a ``bytes`` entry is always ``str``), so
    it can never disagree with the C width the same entry declares.

    >>> entry_py({"code": "A", "ctype": "char", "bytes": True})
    'str'
    >>> entry_py({"code": "D", "ctype": "double"})
    'float'
    >>> entry_py({"code": "X", "ctype": "int64_t"})
    'int'
    """
    if entry_is_bytes(entry):
        return "str"
    return T.scalar_py_annotation(entry["ctype"])


def is_codec_method(m: dict) -> bool:
    """True if method dict *m* packs a codec variant argument."""
    return bool(m.get("codec"))


def is_codec_property(p: dict) -> bool:
    """True if property dict *p* decodes a codec container."""
    return bool(p.get("codec"))


# Deterministic display order for numeric kinds in a `.pyi` union — `int`
# before `float` reads more conventionally than alphabetical (`float | int`).
_KIND_ORDER = {"int": 0, "float": 1}


def _py_kinds(cdc: dict) -> tuple[list[str], list[str]]:
    """Return ``(bytes_py, numeric_py)`` — de-duplicated Python kinds.

    ``bytes_py`` is ``["str"]`` when any entry is a bytes branch (else empty);
    ``numeric_py`` is the distinct ``int``/``float`` kinds of the numeric
    entries, ordered ``int`` before ``float`` for a stable, readable union.
    """
    byte_k: set[str] = set()
    num_k: set[str] = set()
    for e in codec_entries(cdc):
        if entry_is_bytes(e):
            byte_k.add("str")
        else:
            num_k.add(entry_py(e))
    return sorted(byte_k), sorted(num_k, key=lambda k: _KIND_ORDER.get(k, 9))


def codec_py_union(cdc: dict, *, seq: str = "list") -> str:
    """Render the ``.pyi`` type union for a codec value.

    *seq* is the container syntax for the multi-element form: ``"list"`` for a
    decoded (read) dict value, ``"Sequence"`` for an accepted (write) input
    (accepts any sequence, not just a list). A bytes branch contributes only
    ``str`` (never a sequence); each numeric kind contributes both its scalar
    and its sequenced form.

    >>> cdc = {"entries": [
    ...     {"code": "A", "ctype": "char", "bytes": True},
    ...     {"code": "X", "ctype": "int64_t"},
    ...     {"code": "D", "ctype": "double"},
    ... ]}
    >>> codec_py_union(cdc, seq="list")
    'str | int | float | list[int] | list[float]'
    >>> codec_py_union(cdc, seq="Sequence")
    'str | int | float | Sequence[int] | Sequence[float]'
    """
    byte_k, num_k = _py_kinds(cdc)
    parts = [*byte_k, *num_k, *(f"{seq}[{k}]" for k in num_k)]
    return " | ".join(parts)


def validate_codec(name: str, cdc: dict) -> None:
    """Raise :class:`CodecError` if the ``[codec.<name>]`` table is malformed.

    Checks: at least one entry; each entry has a ``code`` and the codes are
    unique; every numeric entry's ``ctype`` is an ``int``/``float`` scalar in
    ``_CTYPE_META`` (a ``bytes`` entry's ctype is decorative and unchecked); the
    ``discriminant`` C type is valid.
    """
    where = f"[codec.{name}]"
    entries = codec_entries(cdc)
    if not entries:
        raise CodecError(f"{where}: needs at least one entry.")

    # `char` is the natural type-code discriminant (a single-byte tag); it is
    # not a _CTYPE_META arithmetic scalar, so accept it explicitly alongside the
    # int-family scalars a discriminant could otherwise be.
    disc = discriminant_ctype(cdc)
    if disc != "char" and disc not in T._CTYPE_META:
        raise CodecError(
            f"{where}: discriminant '{disc}' is not 'char' or a known scalar "
            "C type."
        )

    seen: set[str] = set()
    for e in entries:
        code = e.get("code")
        if not code:
            raise CodecError(f"{where}: an entry is missing 'code'.")
        if code in seen:
            raise CodecError(f"{where}: duplicate code '{code}'.")
        seen.add(code)
        if entry_is_bytes(e):
            continue  # raw-bytes branch: ctype is decorative, decoded as str
        ct = e.get("ctype")
        if not ct or ct not in T._CTYPE_META:
            raise CodecError(
                f"{where}: entry '{code}' has an unknown ctype '{ct}'."
            )
        if T.scalar_py_annotation(ct) not in ("int", "float"):
            raise CodecError(
                f"{where}: entry '{code}' ctype '{ct}' is not an int or float "
                "scalar (only int/float elements, or bytes=true, are supported)."
            )
