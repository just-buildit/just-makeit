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


# ── write pack: PyObject -> discriminant-tagged host buffer -> sink_fn ─────────
#
# A "codec method" declares `codec` + `sink_fn` and, among its `params`, one
# `role = "discriminant"` (the tag that selects a branch) and one
# `role = "variant"` (the value jm packs). Every other param is a fixed scalar/
# string passed straight through. jm generates the whole binding — parse,
# per-code pack of a scalar-or-sequence into a host-order buffer, the sink call,
# and rc->error — so the method body is 100% generated (no hand marshaler).


def _param_role(p: dict) -> str:
    return p.get("role", "fixed")


def method_discriminant(m: dict) -> dict | None:
    """The `role = "discriminant"` param, or None."""
    return next(
        (p for p in m.get("params", []) if _param_role(p) == "discriminant"),
        None,
    )


def method_variant(m: dict) -> dict | None:
    """The `role = "variant"` param, or None."""
    return next(
        (p for p in m.get("params", []) if _param_role(p) == "variant"), None
    )


def method_fixed_params(m: dict) -> list[dict]:
    """The passthrough params (no role / role="fixed"), in declared order."""
    return [p for p in m.get("params", []) if _param_role(p) == "fixed"]


_STRING_TYPES = ("const char *", "char *", "string", "path")


def _fixed_parse(p: dict) -> tuple[str, str, str, str]:
    """Return (decl, fmt char, &addr, call-arg) for a fixed passthrough param."""
    name, t = p["name"], p.get("type", "const char *")
    if t in _STRING_TYPES:
        return f"    const char *{name} = NULL;", "s", f"&{name}", name
    meta = T._CTYPE_META.get(t)
    if not meta:
        raise CodecError(
            f"codec method: fixed param '{name}' has bad type '{t}'"
        )
    return f"    {t} {name} = 0;", meta["fmt"], f"&{name}", name


def validate_codec_method(component: str, m: dict, cdc: dict) -> None:
    """Raise :class:`CodecError` if a codec method is missing required pieces."""
    where = f"{component}.{m.get('name', '?')}"
    if not m.get("sink_fn"):
        raise CodecError(f"{where}: a codec method needs a 'sink_fn'.")
    if method_discriminant(m) is None:
        raise CodecError(f"{where}: needs one param with role='discriminant'.")
    if method_variant(m) is None:
        raise CodecError(f"{where}: needs one param with role='variant'.")


def render_pack(
    component: str,
    Component: str,
    wrapper_prefix: str,
    m: dict,
    cdc: dict,
    guard: str,
    state_expr: str = "self->handle",
) -> tuple[str, str, str]:
    """Render (C body, PyMethodDef line, ``.pyi`` line) for a codec-pack method.

    The generated method parses the fixed params, the single-character
    discriminant (PyArg ``"C"``), and the variant object (``"O"``); switches on
    the discriminant per *cdc* to pack a scalar-or-sequence into a host-order
    buffer of the coded width (the ``bytes`` branch takes the string raw); calls
    ``sink_fn(state, <fixed…>, <disc>, buf, count)``; and maps a non-zero return
    to ``ValueError``. Elements coerce via ``PyFloat_AsDouble`` /
    ``PyLong_AsLongLong`` — the value type is fixed by the codec, not the
    manifest, which is exactly why the pack can be generated.
    """
    validate_codec_method(component, m, cdc)
    name = m["name"]
    fn = f"{wrapper_prefix}_{name}"
    sink = m["sink_fn"]
    disc = method_discriminant(m)
    var = method_variant(m)
    fixed = method_fixed_params(m)
    dname, vname = disc["name"], var["name"]

    # parse: fixed fmts + discriminant "C" (a single char) + variant "O".
    decls, fmts, addrs, kwl = [], [], [], []
    for p in fixed:
        d, f, a, _ = _fixed_parse(p)
        decls.append(d)
        fmts.append(f)
        addrs.append(a)
        kwl.append(f'"{p["name"]}"')
    decls.append(f"    int _{dname}_i = 0;")
    fmts.append("C")
    addrs.append(f"&_{dname}_i")
    kwl.append(f'"{dname}"')
    decls.append(f"    PyObject *{vname} = NULL;")
    fmts.append("O")
    addrs.append(f"&{vname}")
    kwl.append(f'"{vname}"')

    # per-code branches built from the codec entries.
    esz_cases, int_cases, float_cases = [], [], []
    for e in codec_entries(cdc):
        code = e["code"]
        if entry_is_bytes(e):
            esz_cases.append(f"    case '{code}': _is_bytes = 1; break;")
            continue
        ct = e["ctype"]
        is_f = T.scalar_py_annotation(ct) == "float"
        esz_cases.append(
            f"    case '{code}': _esz = sizeof({ct});"
            f"{' _is_float = 1;' if is_f else ''} break;"
        )
        cast = (
            f"      case '{code}': {{ {ct} _v = ({ct})_d;"
            " memcpy(_p, &_v, sizeof _v); break; }"
        )
        (float_cases if is_f else int_cases).append(
            cast if is_f else cast.replace("_d", "_ll")
        )

    fixed_call = "".join(f"{a[1:]}, " for a in addrs[: len(fixed)])  # strip &
    sink_scalar = f"{sink}({state_expr}, {fixed_call}_{dname}, _s, (size_t)_n)"
    sink_buffer = f"{sink}({state_expr}, {fixed_call}_{dname}, _buf, _count)"
    fail = (
        f'        PyErr_SetString(PyExc_ValueError, "{name} failed");\n'
        "        return NULL;"
    )

    body = f"""static PyObject *
{fn}({Component}Object *self, PyObject *args, PyObject *kwds)
{{
{guard}    static char *_kwlist[] = {{ {", ".join(kwl)}, NULL }};
{chr(10).join(decls)}
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{"".join(fmts)}", _kwlist,
                                     {", ".join(addrs)}))
        return NULL;
    char _{dname} = (char)_{dname}_i;

    size_t _esz = 0;
    int _is_float = 0, _is_bytes = 0;
    switch (_{dname}) {{
{chr(10).join(esz_cases)}
    default:
        PyErr_Format(PyExc_ValueError, "unsupported code '%c'", _{dname});
        return NULL;
    }}

    if (_is_bytes) {{
        if (!PyUnicode_Check({vname})) {{
            PyErr_SetString(PyExc_TypeError, "value must be a str");
            return NULL;
        }}
        Py_ssize_t _n = 0;
        const char *_s = PyUnicode_AsUTF8AndSize({vname}, &_n);
        if (!_s)
            return NULL;
        if ({sink_scalar} != 0) {{
{fail}
        }}
        Py_RETURN_NONE;
    }}

    PyObject *_seq = NULL;
    size_t _count = 1;
    if (PySequence_Check({vname}) && !PyUnicode_Check({vname})) {{
        _seq = PySequence_Fast({vname}, "value must be a number or a sequence");
        if (!_seq)
            return NULL;
        _count = (size_t)PySequence_Fast_GET_SIZE(_seq);
    }}
    if (_count == 0) {{
        PyErr_SetString(PyExc_ValueError, "value sequence is empty");
        Py_XDECREF(_seq);
        return NULL;
    }}
    uint8_t *_buf = (uint8_t *)malloc(_count * _esz);
    if (!_buf) {{
        PyErr_NoMemory();
        Py_XDECREF(_seq);
        return NULL;
    }}
    for (size_t _i = 0; _i < _count; _i++) {{
        PyObject *_item = _seq ? PySequence_Fast_GET_ITEM(_seq, _i) : {vname};
        uint8_t *_p = _buf + _i * _esz;
        if (_is_float) {{
            double _d = PyFloat_AsDouble(_item);
            if (_d == -1.0 && PyErr_Occurred())
                goto _err;
            switch (_{dname}) {{
{chr(10).join(float_cases)}
            default: break;
            }}
        }} else {{
            long long _ll = PyLong_AsLongLong(_item);
            if (_ll == -1 && PyErr_Occurred())
                goto _err;
            switch (_{dname}) {{
{chr(10).join(int_cases)}
            default: break;
            }}
        }}
        continue;
    _err:
        free(_buf);
        Py_XDECREF(_seq);
        return NULL;
    }}
    int _rc = {sink_buffer};
    free(_buf);
    Py_XDECREF(_seq);
    if (_rc != 0) {{
{fail}
    }}
    Py_RETURN_NONE;
}}
"""

    pmd = (
        f'    {{"{name}", (PyCFunction)(void *){fn},'
        " METH_VARARGS | METH_KEYWORDS,\n"
        f'     "{name}(...) -- add a codec-typed value."}},\n'
    )
    pyi = "\n".join(render_method_pyi(m, cdc)) + "\n"
    return body, pmd, pyi


def _fixed_pyi(p: dict) -> str:
    """The `.pyi` annotation for a fixed passthrough param."""
    t = p.get("type", "const char *")
    if t in _STRING_TYPES:
        return "str"
    return T.scalar_py_annotation(t)


def render_method_pyi(m: dict, cdc: dict) -> list[str]:
    """The class-method ``.pyi`` lines for a codec-pack method.

    The single renderer for the codec method signature, called by BOTH the
    standalone stub (via :func:`render_pack`) and the module-aggregated stub
    (``_stubs._obj_stub``), so the two peer generators cannot drift. The variant
    input accepts a scalar or any sequence, so it is typed with the ``Sequence``
    form of the codec's Python union.
    """
    name = m["name"]
    disc = method_discriminant(m)
    var = method_variant(m)
    fixed = method_fixed_params(m)
    fixed_sig = "".join(f", {p['name']}: {_fixed_pyi(p)}" for p in fixed)
    union = codec_py_union(cdc, seq="Sequence")
    brief = m.get("doc") or f"{name.replace('_', ' ').capitalize()}."
    return [
        f"    def {name}(self{fixed_sig}, {disc['name']}: str,"
        f" {var['name']}: {union}) -> None:",
        f'        """{brief}"""',
    ]


# ── read decode: discriminant-tagged host buffer -> Python ─────────────────────
#
# A "codec property" declares `codec` + an `entry_fn` cursor (returning a struct
# with a type-code / count / value-pointer) and jm generates the per-entry
# decode that the gh-543 container getter otherwise takes from a hand-written
# `value_fn`. The SAME codec table drives this and the write pack, so a value
# packed with code 'D' reads back a Python float with zero drift.


def _entry_field(p: dict, role: str, default: str) -> str:
    return p.get(f"{role}_field", default)


def render_decode(
    component: str,
    Component: str,
    p: dict,
    cdc: dict,
) -> tuple[str, str, list[str]]:
    """Render (decode-helper C, value expression, extra ``_core.h`` decls).

    The helper is a ``static`` ext function ``<Component>_decode_<pname>`` taking
    the entry struct pointer and returning a new reference: the ``bytes`` branch
    yields ``str``; a numeric branch decodes ``count`` elements to ``int`` /
    ``float`` and (with ``scalar_collapse``) returns a bare scalar when
    ``count == 1``, else a ``list``. The value expression the container getter
    substitutes is ``<helper>(<entry_fn>(self->handle, _i))``. The ``entry_fn``
    is plain C, so it is declared in ``_core.h``; the helper needs ``Python.h``,
    so it is emitted ``static`` in the ext (via the getter's inline ``fwd``).
    """
    pname = p["name"]
    entry_fn = p.get("entry_fn") or f"{component}_{pname}_entry"
    entry_t = p.get("entry_type") or f"{component}_{pname}_t"
    tf = _entry_field(p, "type", "type")
    cf = _entry_field(p, "count", "count")
    vf = _entry_field(p, "value", "value")
    # scalar_collapse is a codec-level decode policy (a value packed as a lone
    # element reads back as a scalar, not a 1-list); a property may override it.
    collapse = bool(p.get("scalar_collapse", cdc.get("scalar_collapse")))
    header = p.get("header")
    helper = f"{Component}_decode_{pname}"

    esz_cases, int_cases, float_cases = [], [], []
    for e in codec_entries(cdc):
        code = e["code"]
        if entry_is_bytes(e):
            esz_cases.append(f"    case '{code}': _is_bytes = 1; break;")
            continue
        ct = e["ctype"]
        is_f = T.scalar_py_annotation(ct) == "float"
        esz_cases.append(
            f"    case '{code}': _esz = sizeof({ct});"
            f"{' _is_float = 1;' if is_f else ''} break;"
        )
        dec = (
            f"        case '{code}': {{ {ct} _v; memcpy(&_v, _p, sizeof _v);"
            f" _it = {{PY}}; break; }}"
        )
        if is_f:
            float_cases.append(
                dec.replace("{PY}", "PyFloat_FromDouble((double)_v)")
            )
        else:
            int_cases.append(
                dec.replace("{PY}", "PyLong_FromLongLong((long long)_v)")
            )

    collapse_block = (
        f"""    if (_e->{cf} == 1) {{
        PyObject *_s = PyList_GET_ITEM(_lst, 0);
        Py_INCREF(_s);
        Py_DECREF(_lst);
        return _s;
    }}
"""
        if collapse
        else ""
    )
    inc = f'#include "{header}"\n' if header else ""
    fn_c = f"""{inc}static PyObject *
{helper}(const {entry_t} *_e)
{{
    size_t _esz = 0;
    int _is_float = 0, _is_bytes = 0;
    switch (_e->{tf}) {{
{chr(10).join(esz_cases)}
    default:
        PyErr_Format(PyExc_ValueError, "unknown code '%c'", _e->{tf});
        return NULL;
    }}
    if (_is_bytes)
        return PyUnicode_FromStringAndSize((const char *)_e->{vf},
                                           (Py_ssize_t)_e->{cf});
    PyObject *_lst = PyList_New((Py_ssize_t)_e->{cf});
    if (!_lst)
        return NULL;
    for (size_t _k = 0; _k < _e->{cf}; _k++) {{
        const uint8_t *_p = (const uint8_t *)_e->{vf} + _k * _esz;
        PyObject *_it = NULL;
        if (_is_float) {{
            switch (_e->{tf}) {{
{chr(10).join(float_cases)}
            default: break;
            }}
        }} else {{
            switch (_e->{tf}) {{
{chr(10).join(int_cases)}
            default: break;
            }}
        }}
        if (!_it) {{
            Py_DECREF(_lst);
            return NULL;
        }}
        PyList_SET_ITEM(_lst, (Py_ssize_t)_k, _it);
    }}
{collapse_block}    return _lst;
}}

"""
    value_expr = f"{helper}({entry_fn}(self->handle, _i))"
    # jm does NOT declare entry_fn or its struct — they are the user's (the read
    # mirror of the write side, where jm never declares the sink_fn). The user
    # declares `const <entry_type> *<entry_fn>(state, i)` and the struct in the
    # object's _core.h (or a `header` the property names, #included above), so
    # the decode helper sees a complete type. jm only calls it.
    return fn_c, value_expr, []


def property_py_type(p: dict, cdc: dict) -> str:
    """The container ``.pyi`` value type for a codec property (read = ``list``)."""
    return codec_py_union(cdc, seq="list")
