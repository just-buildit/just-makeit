"""
_composer.py — code generator for ``kind = "composer"`` modules (gh-287).

A composer turns jm from a one-off binding generator into a *templating engine
that composes objects of objects*: from one declarative manifest it emits the
ergonomic OO surface — CPython **types** living in the ``.so`` — that today is
hand-written pure Python (doppler's ``compose.py``). The whole point is a
self-contained extension: a bare ``import`` of the ``.so`` gives ``Synth`` /
``Segment`` / ``Timeline`` / ``Composer``, no forced Python wrapper, with a
take-it-or-leave-it ``.pyi``.

This module is built on the capsule skeleton (gh-286, :mod:`_capsule`). The C
kernels (accumulation / synth / noise-resolve) stay hand-written; everything
around them — the enum int↔string tables (from the ``[[enum]]`` SSOT, gh-285),
the source/segment marshalling, and the OO types — is generated here.

This slice (C2.2.a) emits the enum tables + the **source type** (e.g. ``Synth``)
and its factory functions; the segment / timeline / composer types and the
JSON / CLI faces land in the following slices.
"""

from __future__ import annotations

from pathlib import Path

from . import _config as C
from . import _enumc
from ._docstring import ClassParam, class_docstring
from ._pyfmt import reflow_pyi

# ── C type / format helpers ──────────────────────────────────────────────────

# PyArg_ParseTupleAndKeywords format char per C scalar type. Enum fields cross
# as Python strings ("s"); a bytes buffer crosses opaquely ("O").
_FMT = {
    "int": "i",
    "double": "d",
    "float": "f",
    "uint32_t": "I",
    "uint64_t": "K",
    "size_t": "n",
}


def _field_fmt(field: dict) -> str:
    if field.get("_ranged"):
        return "O"  # scalar or (lo, hi) — decoded post-parse
    if field.get("enum"):
        return "s"
    if field.get("bytes") or field.get("complex"):
        return "O"  # opaque: a bytes buffer / a numpy complex64 array
    return _FMT[field["type"]]


def _field_is_buffer(field: dict) -> bool:
    """An owned heap array crossing as a Python object — a ``bytes`` pattern or
    a ``complex`` (complex64) stream. Both store ``src-><name>`` /
    ``src->n_<name>`` and need a free in dealloc."""
    return bool(field.get("bytes") or field.get("complex"))


def _field_is_enum(field: dict) -> bool:
    return bool(field.get("enum"))


def _to_py_scalar(ctype: str, expr: str) -> str:
    """C expression building a PyObject from a scalar struct member."""
    if ctype in ("double", "float"):
        return f"PyFloat_FromDouble((double){expr})"
    if ctype == "uint64_t":
        return f"PyLong_FromUnsignedLongLong((unsigned long long){expr})"
    if ctype == "uint32_t":
        return f"PyLong_FromUnsignedLong((unsigned long){expr})"
    if ctype == "size_t":
        return f"PyLong_FromSize_t((size_t){expr})"
    return f"PyLong_FromLong((long){expr})"


# ── enum SSOT → C tables ─────────────────────────────────────────────────────


def _enums_used(cfg: dict, module: str) -> list[str]:
    """Ordered, de-duplicated enum names referenced by the source/segment
    fields of *module* (so we only emit the tables the type actually needs)."""
    seen: list[str] = []
    for tbl in (
        C.composer_source(cfg, module),
        C.composer_segment(cfg, module),
    ):
        for f in tbl.get("fields", []):
            e = f.get("enum")
            if e and e not in seen:
                seen.append(e)
    # gh-317: delegated serializers' enum params need their SSOT tables too.
    for s in C.composer_serializers(cfg, module):
        for p in s.get("params", []):
            e = p.get("enum")
            if e and e not in seen:
                seen.append(e)
    return seen


#: gh-1026: the lookup lives in `_enumc` now, with the tables and the call
#: site it was always meant to travel with. Kept here as a name because this
#: file references it in a dozen emitted snippets, and because three other
#: modules imported it from here — the alias is what makes the move a
#: no-diff change to the generated C.
_ENUM_INDEX_FN = _enumc.INDEX_FN


def render_enum_tables(cfg: dict, module: str) -> str:
    """The lookup plus one table per enum this module references.

    Order **is** the C int (the ``[[enum]]`` SSOT contract — append-only), so
    every face agrees about what a choice means. The emitter is shared with
    every other face (gh-1026); only the SET of enums differs, and that is
    what `_enums_used` decides.
    """
    return _enumc.render_tables(_enums_used(cfg, module), C.enums(cfg))


# ── source type (e.g. Synth) ─────────────────────────────────────────────────


def _ranged_map(table: dict) -> dict[str, str]:
    """``{field_name: WFM_RANGE_* flag macro}`` for a composer source/segment
    ``ranged`` table. A ranged field accepts a ``(lo, hi)`` pair in addition to
    a scalar; the composer redraws it uniformly each repeat. Declared as
    ``ranged = [{name = "freq", flag = "WFM_RANGE_FREQ"}, …]`` — the backing
    struct must carry a ``<name>_hi`` companion and a ``ranged`` flag field."""
    return {r["name"]: r["flag"] for r in table.get("ranged", [])}


def _annotate_ranged(fields: list[dict], rmap: dict[str, str]) -> list[dict]:
    """Tag each field dict with ``_ranged`` (its flag macro) when rangeable."""
    return [
        {**f, "_ranged": rmap[f["name"]]} if f["name"] in rmap else f
        for f in fields
    ]


def _has_ranged(cfg: dict, module: str) -> bool:
    """True when any source or segment field is rangeable (so the shared
    ``_jm_parse_range`` helper is emitted)."""
    return bool(
        _ranged_map(C.composer_source(cfg, module))
        or _ranged_map(C.composer_segment(cfg, module))
    )


# Shared scalar-or-(lo, hi) parser for ranged composer fields. Returns lo/hi as
# doubles + a flag; the caller casts to the field's C type. A scalar leaves
# *is_range 0; a 2-sequence (not str/bytes) sets it. Reproducible draws happen
# in the composer core — this only records the bounds the user passed.
_RANGE_PARSE_FN = "\n".join(
    [
        "static int",
        "_jm_parse_range(PyObject *o, double *lo, double *hi, int *is_range)",
        "{",
        "    if (PySequence_Check(o) && !PyUnicode_Check(o)",
        "        && !PyBytes_Check(o)) {",
        "        if (PySequence_Size(o) != 2) {",
        "            PyErr_SetString(PyExc_ValueError,",
        '                            "range must be a (low, high) pair");',
        "            return 0;",
        "        }",
        "        PyObject *a = PySequence_GetItem(o, 0);",
        "        PyObject *b = PySequence_GetItem(o, 1);",
        "        double dlo = a ? PyFloat_AsDouble(a) : -1.0;",
        "        double dhi = b ? PyFloat_AsDouble(b) : -1.0;",
        "        Py_XDECREF(a);",
        "        Py_XDECREF(b);",
        "        if (PyErr_Occurred())",
        "            return 0;",
        "        *lo = dlo;",
        "        *hi = dhi;",
        "        *is_range = 1;",
        "        return 1;",
        "    }",
        "    {",
        "        double v = PyFloat_AsDouble(o);",
        "        if (PyErr_Occurred())",
        "            return 0;",
        "        *lo = v;",
        "        *is_range = 0;",
        "    }",
        "    return 1;",
        "}",
        "",
    ]
)


def render_range_helper(cfg: dict, module: str) -> str:
    """Emit the shared ranged-field parser once, when the module uses ranges."""
    return _RANGE_PARSE_FN if _has_ranged(cfg, module) else ""


def _source_fields(cfg: dict, module: str) -> list[dict]:
    tbl = C.composer_source(cfg, module)
    return _annotate_ranged(list(tbl.get("fields", [])), _ranged_map(tbl))


def _source_generates(cfg: dict, module: str) -> dict | None:
    """The source's standalone-generation config, with generic defaults filled.

    ``[module.X.source.generates]`` opts the source type into standalone sample
    generation by delegating to a *composed generator* object. Keys:

    ``generator``    the composed generator object name (e.g. ``"wfm_synth"``);
    ``bridge_fn``    a straight-C function ``<state_type> *fn(const <struct> *,
                     double fs)`` the project writes (its construction algorithm —
                     no CPython); jm emits the binding that calls it.

    Defaults (overridable): ``state_type = <generator>_state_t``,
    ``steps_fn/step_fn/reset_fn/destroy_fn = <generator>_{steps,step,reset,
    destroy}``, ``header = <generator>/<generator>_core.h``,
    ``output_type = "float complex"`` (NumPy ``complex64``). Returns ``None`` when
    the source declares no generation."""
    g = C.composer_source(cfg, module).get("generates")
    if not g:
        return None
    gen = g["generator"]
    return {
        "generator": gen,
        "bridge_fn": g["bridge_fn"],
        "state_type": g.get("state_type", f"{gen}_state_t"),
        "steps_fn": g.get("steps_fn", f"{gen}_steps"),
        "step_fn": g.get("step_fn", f"{gen}_step"),
        "reset_fn": g.get("reset_fn", f"{gen}_reset"),
        "destroy_fn": g.get("destroy_fn", f"{gen}_destroy"),
        "header": g.get("header", f"{gen}/{gen}_core.h"),
        "output_type": g.get("output_type", "float complex"),
    }


def _source_computed(cfg: dict, module: str) -> list[dict]:
    """The source's computed (derived) read-only properties (feature 6).

    ``[[module.X.source.computed]]`` adds a **read-only** attribute on the source
    type whose value is computed in C from the struct — a derived quantity that
    is not a stored field (so it never goes stale when the fields it depends on
    are mutated). Each entry:

    ``name``    the Python attribute (e.g. ``"n_samples"``);
    ``type``    the C return type (``size_t`` / ``double`` / ``int`` / …),
                converted to Python like any scalar field;
    ``fn``      a straight-C function ``<type> fn(const <struct> *)`` the project
                writes (the derivation — no CPython); jm emits the getset that
                calls it.

    Optional ``doc`` (getset docstring). Generic — mirrors ``generates``'s
    project-C-function seam; returns ``[]`` when none are declared.

    A computed name that collides with a stored field (or the always-present
    ``fs``) is a manifest error — both would emit a getset row for the same
    attribute and the second would silently shadow the first — so it is
    rejected here (a derived quantity must not share a stored field's name)."""
    computed = list(C.composer_source(cfg, module).get("computed", []))
    if computed:
        taken = {f["name"] for f in _source_fields(cfg, module)} | {"fs"}
        clash = sorted(c["name"] for c in computed if c["name"] in taken)
        if clash:
            raise ValueError(
                "computed source property name collides with a stored field: "
                + ", ".join(clash)
            )
    return computed


def render_source_type(cfg: dict, module: str) -> str:
    """Emit the source ``PyTypeObject`` (e.g. ``Synth``): a config object
    wrapping the backing C struct, with a keyword ``tp_init``, per-field getset
    (enum fields as strings, scalars as numbers, a ``bytes`` buffer), and the
    factory module functions (``tone`` / ``bpsk`` / …) that preset the
    discriminant enum field."""
    src = C.composer_source(cfg, module)
    struct = src["struct"]
    tname = src["type_name"]  # Python class name, e.g. "Synth"
    fields = _source_fields(cfg, module)
    pkg = C.project_name(cfg)
    pkg_path = C.capsule_package(cfg, module) or C.module_paths(module).pypath
    dotted = f"{pkg}.{pkg_path.replace('/', '.')}.{tname}"

    obj = f"{tname}Object"
    type_obj = f"{tname}Type"

    # Optional standalone generation: the source delegates to a *composed
    # generator* object, built once from the source struct by a project-provided
    # straight-C bridge function (no CPython glue). jm emits the steps/step/reset
    # plumbing; the bridge encodes the construction algorithm. Generic — any
    # source-of-a-generator opts in via [module.X.source.generates].
    gen = _source_generates(cfg, module)

    # Feature 2 — input ergonomics, generated into tp_init so the .so is the API:
    #  (a) field aliases: a ctor kwarg accepted as a stand-in for the canonical
    #      field (e.g. f_start -> freq); folded before parsing, both-given errors.
    #  (b) bit_pattern coercion: a bytes field accepts a 0/1 pattern as bytes, a
    #      binary/hex string ("0101" / "0xAA55"), or a sequence of ints.
    aliases = [(a, f["name"]) for f in fields for a in f.get("aliases", [])]
    bits_coerce = any(
        f.get("bytes") and f.get("coerce") == "bit_pattern" for f in fields
    )

    parts: list[str] = []

    # struct: backing config + an extra fs (segment owns it in composition, but
    # the source carries it for standalone use). With generation, a lazily-built
    # composed-generator handle (NULL until the first steps/step) rides along.
    gen_field = f"\n    {gen['state_type']} *_gen;" if gen else ""
    parts.append(f"""typedef struct {{
    PyObject_HEAD
    {struct} src;
    double   fs;{gen_field}
}} {obj};
""")

    # dealloc — destroy any built generator, then free every owned buffer
    # field (a bytes pattern and/or a complex stream).
    gen_dtor = (
        f"    if (self->_gen) {gen['destroy_fn']}(self->_gen);\n"
        if gen
        else ""
    )
    free_bufs = "".join(
        f"    free(self->src.{f['name']});\n"
        for f in fields
        if _field_is_buffer(f)
    )
    parts.append(f"""static void
{tname}_dealloc({obj} *self)
{{
{gen_dtor}{free_bufs}    Py_TYPE(self)->tp_free((PyObject *)self);
}}
""")

    # tp_init — keyword args, all optional with manifest defaults.
    kwlist = ", ".join(f'"{f["name"]}"' for f in fields)
    fmt = "|" + "".join(_field_fmt(f) for f in fields) + "d"  # trailing fs
    decls: list[str] = []
    addrs: list[str] = []
    for f in fields:
        n = f["name"]
        if _field_is_enum(f):
            default = f.get("default", "")
            decls.append(f'    const char *{n} = "{default}";')
            addrs.append(f"&{n}")
        elif _field_is_buffer(f) or f.get("_ranged"):
            decls.append(f"    PyObject *{n} = NULL;")
            addrs.append(f"&{n}")
        else:
            default = f.get("default", "0")
            decls.append(f"    {f['type']} {n} = {default};")
            addrs.append(f"&{n}")
    decls_s = "\n".join(decls)
    addrs_s = ", ".join(addrs)

    # post-parse: validate enums into the struct ints, assign scalars, attach
    # bytes.
    assign: list[str] = []
    for f in fields:
        n = f["name"]
        if _field_is_enum(f):
            e = f["enum"]
            _v = _enumc.validate_c(
                n,
                e,
                C.enums(cfg),
                result="_i",
                fail="return -1;",
                indent="        ",
            )
            assign.append(f"    {{\n{_v}\n        self->src.{n} = _i;\n    }}")
        elif f.get("bytes"):
            # Per-field destination: several bytes fields on one source
            # (e.g. a DSSS burst's acq_code/data_code/sync + payload) must
            # each land in their own struct arrays, not all in `bits`.
            assign.append(
                f"    if (!_attach_bytes(&self->src.{n}, "
                f"&self->src.n_{n}, {n}))\n        return -1;"
            )
        elif f.get("complex"):
            assign.append(f"""    if (!_attach_{n}(&self->src, {n}))
        return -1;""")
        elif f.get("_ranged"):
            flag = f["_ranged"]
            ct = f["type"]
            default = f.get("default", "0")
            assign.append(f"""    if ({n} != NULL) {{
        double _lo, _hi;
        int    _r;
        if (!_jm_parse_range({n}, &_lo, &_hi, &_r))
            return -1;
        self->src.{n} = ({ct})_lo;
        if (_r) {{
            self->src.{n}_hi = ({ct})_hi;
            self->src.ranged |= {flag};
        }} else {{
            self->src.ranged &= ~(unsigned){flag};
        }}
    }} else {{
        self->src.{n} = ({ct}){default};
    }}""")
        else:
            assign.append(f"    self->src.{n} = {n};")
    assign_s = "\n".join(assign)

    store_bits = """    *dst   = buf;
    *n_dst = (size_t)nb;
    return 1;"""
    if bits_coerce:
        attach_doc = (
            "Coerce a 0/1 pattern (bytes | binary/hex str | int sequence)"
        )
        attach_body = f"""    if (PyBytes_Check(obj)) {{
        Py_ssize_t nb = PyBytes_GET_SIZE(obj);
        if (nb <= 0)
            return 1;
        uint8_t *buf = (uint8_t *)malloc((size_t)nb);
        if (!buf) {{ PyErr_NoMemory(); return 0; }}
        memcpy(buf, PyBytes_AS_STRING(obj), (size_t)nb);
{store_bits}
    }}
    if (PyUnicode_Check(obj)) {{
        Py_ssize_t slen;
        const char *s = PyUnicode_AsUTF8AndSize(obj, &slen);
        if (!s)
            return 0;
        if (slen >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {{
            Py_ssize_t nd = slen - 2, nb = nd * 4; /* hex digit -> 4 bits MSB */
            uint8_t *buf = (uint8_t *)malloc(nb ? (size_t)nb : 1);
            if (!buf) {{ PyErr_NoMemory(); return 0; }}
            for (Py_ssize_t i = 0; i < nd; i++) {{
                char c = s[2 + i];
                int v = (c >= '0' && c <= '9')   ? c - '0'
                        : (c >= 'a' && c <= 'f') ? c - 'a' + 10
                        : (c >= 'A' && c <= 'F') ? c - 'A' + 10
                                                 : -1;
                if (v < 0) {{
                    free(buf);
                    PyErr_SetString(PyExc_ValueError, "invalid hex digit");
                    return 0;
                }}
                for (int b = 0; b < 4; b++)
                    buf[i * 4 + b] = (uint8_t)((v >> (3 - b)) & 1);
            }}
{store_bits}
        }}
        uint8_t *buf = (uint8_t *)malloc(slen ? (size_t)slen : 1);
        if (!buf) {{ PyErr_NoMemory(); return 0; }}
        for (Py_ssize_t i = 0; i < slen; i++) {{
            if (s[i] != '0' && s[i] != '1') {{
                free(buf);
                PyErr_SetString(PyExc_ValueError,
                                "bit string must be 0/1 or '0x..' hex");
                return 0;
            }}
            buf[i] = (uint8_t)(s[i] - '0');
        }}
        Py_ssize_t nb = slen;
{store_bits}
    }}
    {{
        PyObject *seq = PySequence_Fast(
            obj, "bits must be bytes, a 0/1 string, or a sequence of ints");
        if (!seq)
            return 0;
        Py_ssize_t nb = PySequence_Fast_GET_SIZE(seq);
        uint8_t *buf = (uint8_t *)malloc(nb ? (size_t)nb : 1);
        if (!buf) {{ Py_DECREF(seq); PyErr_NoMemory(); return 0; }}
        for (Py_ssize_t i = 0; i < nb; i++) {{
            long v = PyLong_AsLong(PySequence_Fast_GET_ITEM(seq, i));
            if (v == -1 && PyErr_Occurred()) {{
                free(buf);
                Py_DECREF(seq);
                return 0;
            }}
            buf[i] = (uint8_t)(v != 0);
        }}
        Py_DECREF(seq);
{store_bits}
    }}"""
    else:
        attach_doc = "Copy a Python bytes (0/1 pattern) or None"
        attach_body = f"""    if (!PyBytes_Check(obj)) {{
        PyErr_SetString(PyExc_TypeError, "bits must be bytes or None");
        return 0;
    }}
    Py_ssize_t nb = PyBytes_GET_SIZE(obj);
    if (nb <= 0)
        return 1;
    uint8_t *buf = (uint8_t *)malloc((size_t)nb);
    if (!buf) {{
        PyErr_NoMemory();
        return 0;
    }}
    memcpy(buf, PyBytes_AS_STRING(obj), (size_t)nb);
{store_bits}"""

    # Alias-folding preamble (feature 2a): rename alias kwargs to their canonical
    # field before parsing. kwds is copied only when an alias is actually present
    # (the common no-alias call pays nothing).
    if aliases:
        folds = []
        for a, canon in aliases:
            folds.append(f"""        {{
            PyObject *_a = PyDict_GetItemString(kwds, "{a}");
            if (_a) {{
                if (!_kw_owned) {{
                    _kw = PyDict_Copy(kwds);
                    if (!_kw) return -1;
                    _kw_owned = 1;
                }}
                if (PyDict_GetItemString(_kw, "{canon}")) {{
                    PyErr_SetString(PyExc_TypeError,
                        "{canon} and {a} are aliases — pass only one");
                    Py_DECREF(_kw);
                    return -1;
                }}
                if (PyDict_SetItemString(_kw, "{canon}", _a) < 0
                    || PyDict_DelItemString(_kw, "{a}") < 0) {{
                    Py_DECREF(_kw);
                    return -1;
                }}
            }}
        }}""")
        alias_pre = (
            "    PyObject *_kw = kwds;\n    int _kw_owned = 0;\n"
            "    if (kwds) {\n" + "\n".join(folds) + "\n    }\n"
        )
        parse_kw = "_kw"
        parse_fail = (
            "        if (_kw_owned) Py_DECREF(_kw);\n        return -1;"
        )
        parse_ok = "    if (_kw_owned) Py_DECREF(_kw);\n"
    else:
        alias_pre = ""
        parse_kw = "kwds"
        parse_fail = "        return -1;"
        parse_ok = ""

    # One _attach_<name> per complex64 stream field — coerce a numpy complex64
    # array (or None) into an owned src-><name> / src->n_<name>.
    for f in fields:
        if not f.get("complex"):
            continue
        cn = f["name"]
        parts.append(f"""/* Copy a numpy complex64 array (or None) into \
src->{cn} (owned). */
static int
_attach_{cn}({struct} *src, PyObject *obj)
{{
    free(src->{cn});
    src->{cn}   = NULL;
    src->n_{cn} = 0;
    if (!obj || obj == Py_None)
        return 1;
    PyArrayObject *_arr = (PyArrayObject *)PyArray_FROM_OTF(
        obj, NPY_COMPLEX64, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_FORCECAST);
    if (!_arr)
        return 0;
    Py_ssize_t _n = PyArray_SIZE(_arr);
    if (_n <= 0) {{ Py_DECREF(_arr); return 1; }}
    float _Complex *_buf
        = (float _Complex *)malloc((size_t)_n * sizeof *_buf);
    if (!_buf) {{ Py_DECREF(_arr); PyErr_NoMemory(); return 0; }}
    memcpy(_buf, PyArray_DATA(_arr), (size_t)_n * sizeof *_buf);
    Py_DECREF(_arr);
    src->{cn}   = _buf;
    src->n_{cn} = (size_t)_n;
    return 1;
}}
""")

    parts.append(f"""/* {attach_doc} into an owned *dst/*n_dst (one shared
 * coercer; each bytes field passes its own struct destination). */
static int
_attach_bytes(uint8_t **dst, size_t *n_dst, PyObject *obj)
{{
    free(*dst);
    *dst   = NULL;
    *n_dst = 0;
    if (!obj || obj == Py_None)
        return 1;
{attach_body}
}}

static int
{tname}_init({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{{kwlist}, "fs", NULL}};
{decls_s}
    double fs = 1e6;
{alias_pre}    if (!PyArg_ParseTupleAndKeywords(args, {parse_kw}, "{fmt}", kwlist,
            {addrs_s}, &fs)) {{
{parse_fail}
    }}
{parse_ok}    self->fs = fs;
{(f"    if (self->_gen) {{ {gen['destroy_fn']}(self->_gen); self->_gen = NULL; }}" + chr(10)) if gen else ""}{assign_s}
    return 0;
}}
""")

    # getset table.
    getset_fns: list[str] = []
    getset_rows: list[str] = []
    for f in fields:
        n = f["name"]
        if _field_is_enum(f):
            e = f["enum"]
            # gh-1026: the shared refusal, so a property setter and a
            # constructor argument for the same enum say the same thing.
            _enum_set = _enumc.validate_c(
                n,
                e,
                C.enums(cfg),
                src="s",
                result="_i",
                fail="return -1;",
            )
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    return PyUnicode_FromString(_enum_{e}[self->src.{n}]);
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    const char *s = PyUnicode_AsUTF8(value);
    if (!s) return -1;
{_enum_set}
    self->src.{n} = _i;
    return 0;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )
        elif f.get("bytes"):
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    if (self->src.{n} && self->src.n_{n})
        return PyBytes_FromStringAndSize(
            (const char *)self->src.{n}, (Py_ssize_t)self->src.n_{n});
    Py_RETURN_NONE;
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    return _attach_bytes(&self->src.{n}, &self->src.n_{n}, value) ? 0 : -1;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )
        elif f.get("complex"):
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    if (self->src.{n} && self->src.n_{n}) {{
        npy_intp _d[1] = {{(npy_intp)self->src.n_{n}}};
        PyObject *_a = PyArray_SimpleNew(1, _d, NPY_COMPLEX64);
        if (!_a) return NULL;
        memcpy(PyArray_DATA((PyArrayObject *)_a), self->src.{n},
               self->src.n_{n} * sizeof(float _Complex));
        return _a;
    }}
    Py_RETURN_NONE;
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    return _attach_{n}(&self->src, value) ? 0 : -1;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )
        elif f.get("_ranged"):
            # scalar, or (lo, hi) when the field's ranged bit is set.
            flag = f["_ranged"]
            ct = f["type"]
            to_py = _to_py_scalar(ct, f"self->src.{n}")
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    if (self->src.ranged & {flag})
        return Py_BuildValue("(dd)", (double)self->src.{n},
                             (double)self->src.{n}_hi);
    return {to_py};
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    double _lo, _hi;
    int    _r;
    if (!_jm_parse_range(value, &_lo, &_hi, &_r))
        return -1;
    self->src.{n} = ({ct})_lo;
    if (_r) {{
        self->src.{n}_hi = ({ct})_hi;
        self->src.ranged |= {flag};
    }} else {{
        self->src.ranged &= ~(unsigned){flag};
    }}
    return 0;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )
        else:
            ctype = f["type"]
            to_py = _to_py_scalar(ctype, f"self->src.{n}")
            if ctype in ("double", "float"):
                store = (
                    f"    self->src.{n} = ({ctype})PyFloat_AsDouble(value);"
                )
                guard = ""
            elif ctype in ("uint64_t",):
                store = (
                    f"    self->src.{n} = "
                    f"({ctype})PyLong_AsUnsignedLongLong(value);"
                )
                guard = ""
            else:
                store = f"    self->src.{n} = ({ctype})PyLong_AsLong(value);"
                guard = ""
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    return {to_py};
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;{guard}
{store}
    if (PyErr_Occurred()) return -1;
    return 0;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )

    # fs getset (always present).
    getset_fns.append(f"""static PyObject *
{tname}_get_fs({obj} *self, void *closure)
{{
    (void)closure;
    return PyFloat_FromDouble(self->fs);
}}
static int
{tname}_set_fs({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    self->fs = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;
    return 0;
}}""")
    getset_rows.append(
        f'    {{"fs", (getter){tname}_get_fs, (setter){tname}_set_fs, '
        "NULL, NULL},"
    )

    # Feature 6 — computed (derived) read-only properties: a value computed in C
    # from the struct by a project straight-C fn (no CPython), so it never goes
    # stale when its inputs are mutated. Generic; opt-in via
    # [[module.X.source.computed]]. Read-only (NULL setter).
    for c in _source_computed(cfg, module):
        n, ct = c["name"], c["type"]
        to_py = _to_py_scalar(ct, f"{c['fn']}(&self->src)")
        doc = c.get("doc", "")
        doc_c = f'"{doc}"' if doc else "NULL"
        getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    return {to_py};
}}""")
        getset_rows.append(
            f'    {{"{n}", (getter){tname}_get_{n}, NULL, {doc_c}, NULL}},'
        )

    parts.append("\n".join(getset_fns))
    parts.append(f"""
static PyGetSetDef {tname}_getset[] = {{
{chr(10).join(getset_rows)}
    {{NULL, NULL, NULL, NULL, NULL}}
}};
""")

    # Standalone generation methods (steps/step/reset) — emitted only when the
    # source declares a composed generator. Each lazily builds the generator via
    # the project's straight-C bridge_fn, then delegates; the handle is cached on
    # the instance (freed in dealloc).
    tp_methods_slot = ""
    if gen:
        out_t = gen["output_type"]
        parts.append(f"""/* Lazily build the composed generator from this source's config. */
static int
{tname}_ensure_gen({obj} *self)
{{
    if (!self->_gen) {{
        self->_gen = {gen["bridge_fn"]}(&self->src, self->fs);
        if (!self->_gen) {{
            PyErr_SetString(PyExc_RuntimeError,
                            "{gen["bridge_fn"]} returned NULL");
            return -1;
        }}
    }}
    return 0;
}}

static PyObject *
{tname}_steps({obj} *self, PyObject *args)
{{
    Py_ssize_t n;
    if (!PyArg_ParseTuple(args, "n", &n))
        return NULL;
    if (n < 0) {{
        PyErr_SetString(PyExc_ValueError, "n must be >= 0");
        return NULL;
    }}
    if ({tname}_ensure_gen(self) < 0)
        return NULL;
    npy_intp dims[1] = {{ n }};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_COMPLEX64);
    if (!arr)
        return NULL;
    {out_t} *out = ({out_t} *)PyArray_DATA((PyArrayObject *)arr);
    Py_BEGIN_ALLOW_THREADS
    {gen["steps_fn"]}(self->_gen, out, (size_t)n);
    Py_END_ALLOW_THREADS
    return arr;
}}

static PyObject *
{tname}_step({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if ({tname}_ensure_gen(self) < 0)
        return NULL;
    {out_t} y = {gen["step_fn"]}(self->_gen);
    return PyComplex_FromDoubles(crealf(y), cimagf(y));
}}

static PyObject *
{tname}_reset({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if (self->_gen)
        {gen["reset_fn"]}(self->_gen);
    Py_RETURN_NONE;
}}

static PyMethodDef {tname}_methods[] = {{
    {{"steps", (PyCFunction){tname}_steps, METH_VARARGS,
     "steps(n) -> complex64[n] — generate n samples standalone."}},
    {{"step", (PyCFunction){tname}_step, METH_NOARGS,
     "step() -> complex — generate one sample standalone."}},
    {{"reset", (PyCFunction){tname}_reset, METH_NOARGS,
     "reset() -> None — rewind the generator to sample 0."}},
    {{NULL, NULL, 0, NULL}}
}};
""")
        tp_methods_slot = f"\n    .tp_methods   = {tname}_methods,"

    parts.append(f"""static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new       = PyType_GenericNew,
    .tp_init      = (initproc){tname}_init,
    .tp_dealloc   = (destructor){tname}_dealloc,
    .tp_getset    = {tname}_getset,{tp_methods_slot}
    .tp_doc       = PyDoc_STR("{tname} — one composable source configuration."),
}};
""")

    # factories: preset the discriminant enum field, forward the rest.
    oo = C.composer_oo(cfg, module)
    disc = oo.get("discriminant")
    factories = oo.get("factories", [])
    if disc and factories:
        parts.append(f"""/* Factory shared body: inject {{{disc}: <name>}} into kwds, call the type. */
static PyObject *
_{tname}_factory(const char *kind, PyObject *args, PyObject *kwds)
{{
    PyObject *k = kwds ? PyDict_Copy(kwds) : PyDict_New();
    if (!k) return NULL;
    PyObject *v = PyUnicode_FromString(kind);
    if (!v || PyDict_SetItemString(k, "{disc}", v) < 0) {{
        Py_XDECREF(v);
        Py_DECREF(k);
        return NULL;
    }}
    Py_DECREF(v);
    PyObject *r = PyObject_Call((PyObject *)&{type_obj}, args, k);
    Py_DECREF(k);
    return r;
}}
""")
        for fac in factories:
            parts.append(f"""static PyObject *
_factory_{fac}(PyObject *mod, PyObject *args, PyObject *kwds)
{{
    (void)mod;
    return _{tname}_factory("{fac}", args, kwds);
}}
""")

    return "\n".join(parts)


def factory_method_rows(cfg: dict, module: str) -> list[str]:
    """PyMethodDef rows for the source factories (for the module table)."""
    oo = C.composer_oo(cfg, module)
    rows = []
    for fac in oo.get("factories", []):
        rows.append(
            f'    {{"{fac}", (PyCFunction)(void (*)(void))_factory_{fac}, '
            f"METH_VARARGS | METH_KEYWORDS,\n"
            f'     "{fac}(**kw) -> {C.composer_source(cfg, module)["type_name"]}"}},'
        )
    return rows


# ── segment type (e.g. Segment) ──────────────────────────────────────────────


def _from_py_scalar(ctype: str, obj: str) -> str:
    """C expression converting a PyObject to a scalar C value."""
    if ctype in ("double", "float"):
        return f"({ctype})PyFloat_AsDouble({obj})"
    if ctype == "uint64_t":
        return f"(uint64_t)PyLong_AsUnsignedLongLong({obj})"
    if ctype == "uint32_t":
        return f"(uint32_t)PyLong_AsUnsignedLong({obj})"
    if ctype == "size_t":
        return f"(size_t)PyLong_AsSize_t({obj})"
    return f"({ctype})PyLong_AsLong({obj})"


def _segment_fields(cfg: dict, module: str) -> list[dict]:
    tbl = C.composer_segment(cfg, module)
    return _annotate_ranged(list(tbl.get("fields", [])), _ranged_map(tbl))


def _segment_flat_fields(cfg: dict, module: str) -> list[dict]:
    """Source fields a single-source ``Segment`` exposes inline (feature 4).

    When ``[module.X.segment] flat_sources = true``, a Segment built from one
    source proxies that source's fields as read-only attributes
    (``segment.freq`` → ``segment.sources[0].freq``) — the flat single-source
    view that lets a project drop a hand-written ``__getattr__`` fallback.
    Generic over any composer: the names come from ``source.fields``. A name
    that collides with a segment-level getset (``sources`` or a segment scalar)
    is skipped so the segment's own attribute always wins. Returns ``[]`` when
    the segment does not opt in."""
    if not C.composer_segment(cfg, module).get("flat_sources"):
        return []
    taken = {"sources"} | {f["name"] for f in _segment_fields(cfg, module)}
    return [
        f
        for f in C.composer_source(cfg, module).get("fields", [])
        if f["name"] not in taken
    ]


def render_segment_type(cfg: dict, module: str) -> str:
    """Emit the ``Segment`` ``PyTypeObject``: segment-level scalars (fs /
    num_samples / off_samples) plus a Python list of source objects.

    Two faces, matching compose.py: the inline single-source constructor
    (``Segment(type="tone", num_samples=…)`` — forwards the source fields to the
    source type and wraps the one result) and the multi-source ``sum``
    classmethod (``Segment.sum(*sources, num_samples=…)``). The backing
    ``wfm_segment_t[]`` is built later by the Composer; the type itself is pure
    Python-side data, so it needs no backing struct."""
    seg = C.composer_segment(cfg, module)
    tname = seg["type_name"]  # e.g. "Segment"
    src_tname = C.composer_source(cfg, module)["type_name"]  # e.g. "Synth"
    fields = _segment_fields(cfg, module)
    pkg = C.project_name(cfg)
    pkg_path = C.capsule_package(cfg, module) or C.module_paths(module).pypath
    dotted = f"{pkg}.{pkg_path.replace('/', '.')}.{tname}"

    obj = f"{tname}Object"
    type_obj = f"{tname}Type"
    src_type_obj = f"{src_tname}Type"
    # The Timeline type `add` sequences into; declared (and defined) after
    # Segment, so it is forward-declared here.
    tl_tname = C.composer_timeline(cfg, module).get("type_name")

    # Ranged segment fields (e.g. off_samples) carry a <name>_hi companion and
    # share one `ranged` bitmask — mirroring the backing wfm_segment_t — so a
    # (lo, hi) draw survives into the built struct and back through from_json.
    ranged_fields = [f for f in fields if f.get("_ranged")]
    members = "".join(f"    {f['type']} {f['name']};\n" for f in fields)
    if ranged_fields:
        members += "    unsigned ranged;\n"
        members += "".join(
            f"    {f['type']} {f['name']}_hi;\n" for f in ranged_fields
        )
    parts: list[str] = []
    # Forward declaration — the `sum` classmethod (emitted before the type
    # definition) allocates via the type object.
    parts.append(f"static PyTypeObject {type_obj};\n")
    if tl_tname:
        parts.append(f"static PyTypeObject {tl_tname}Type;\n")
    parts.append(f"""typedef struct {{
    PyObject_HEAD
    PyObject *sources;   /* list of {src_tname}, length >= 1 */
{members}}} {obj};
""")

    parts.append(f"""static void
{tname}_dealloc({obj} *self)
{{
    Py_XDECREF(self->sources);
    Py_TYPE(self)->tp_free((PyObject *)self);
}}
""")

    # defaults applied before extraction. An enum default is a string in
    # the manifest ("auto"); the struct member is the SSOT int, so map it
    # at codegen time (gh-460 — segment fields can be enums, like source
    # fields always could).
    enums = C.enums(cfg)

    def _default(f):
        if _field_is_enum(f):
            values = enums.get(f["enum"], [])
            d = f.get("default", "")
            return str(values.index(d)) if d in values else "0"
        if f.get("default") not in (None, ""):
            return f["default"]
        return "0"

    set_defaults = "\n".join(
        [f"    self->{f['name']} = {_default(f)};" for f in fields]
        + (["    self->ranged = 0;"] if ranged_fields else [])
        + [f"    self->{f['name']}_hi = 0;" for f in ranged_fields]
    )

    # extract a segment field from a kwargs dict; *delete* (init) or leave
    # (sum). Shared body emitted as a macro-free inline block per field. A
    # ranged field accepts a scalar or a (lo, hi) pair (→ ranged bit + _hi).
    def _extract_block(f, *, delete: bool):
        n, ct = f["name"], f["type"]
        dele = (
            f'        if (PyDict_DelItemString(kw, "{n}") < 0) goto fail;\n'
            if delete
            else ""
        )
        if _field_is_enum(f):
            e = f["enum"]
            body = (
                "            const char *_s = PyUnicode_AsUTF8(_o);\n"
                "            if (!_s) goto fail;\n"
                + _enumc.validate_c(
                    n,
                    e,
                    C.enums(cfg),
                    src="_s",
                    result="_i",
                    fail="goto fail;",
                    indent="            ",
                )
                + "\n"
                f"            self->{n} = _i;\n"
            )
        elif f.get("_ranged"):
            flag = f["_ranged"]
            body = (
                "            double _lo, _hi;\n"
                "            int    _r;\n"
                "            if (!_jm_parse_range(_o, &_lo, &_hi, &_r))\n"
                "                goto fail;\n"
                f"            self->{n} = ({ct})_lo;\n"
                f"            if (_r) {{ self->{n}_hi = ({ct})_hi;"
                f" self->ranged |= {flag}; }}\n"
                f"            else {{ self->ranged &= ~(unsigned){flag}; }}\n"
            )
        else:
            conv = _from_py_scalar(ct, "_o")
            body = (
                f"            self->{n} = {conv};\n"
                "            if (PyErr_Occurred()) goto fail;\n"
            )
        return (
            f'    {{\n        PyObject *_o = PyDict_GetItemString(kw, "{n}");\n'
            f"        if (_o) {{\n"
            f"{body}"
            f"{dele}        }}\n    }}"
        )

    init_extracts = "\n".join(_extract_block(f, delete=True) for f in fields)
    sum_extracts = "\n".join(_extract_block(f, delete=False) for f in fields)

    # tp_init — single-source inline: forward leftover args/kwds to the source
    # type, wrap the one source.
    parts.append(f"""static int
{tname}_init({obj} *self, PyObject *args, PyObject *kwds)
{{
{set_defaults}
    PyObject *kw = kwds ? PyDict_Copy(kwds) : PyDict_New();
    if (!kw)
        return -1;
{init_extracts}
    PyObject *one = PyObject_Call((PyObject *)&{src_type_obj}, args, kw);
    Py_DECREF(kw);
    if (!one)
        return -1;
    PyObject *list = PyList_New(1);
    if (!list) {{
        Py_DECREF(one);
        return -1;
    }}
    PyList_SET_ITEM(list, 0, one); /* steals */
    Py_XSETREF(self->sources, list);
    return 0;
fail:
    Py_DECREF(kw);
    return -1;
}}
""")

    # sum classmethod — *sources positional + segment kwargs.
    parts.append(f"""static PyObject *
{tname}_sum(PyObject *cls, PyObject *args, PyObject *kwds)
{{
    (void)cls;
    Py_ssize_t nsrc = PyTuple_GET_SIZE(args);
    if (nsrc < 1) {{
        PyErr_SetString(PyExc_ValueError,
                        "{tname}.sum needs at least one source");
        return NULL;
    }}
    PyObject *list = PyList_New(nsrc);
    if (!list)
        return NULL;
    for (Py_ssize_t i = 0; i < nsrc; i++) {{
        PyObject *it = PyTuple_GET_ITEM(args, i);
        if (!PyObject_TypeCheck(it, &{src_type_obj})) {{
            PyErr_SetString(PyExc_TypeError,
                            "{tname}.sum sources must be {src_tname}");
            Py_DECREF(list);
            return NULL;
        }}
        Py_INCREF(it);
        PyList_SET_ITEM(list, i, it);
    }}
    {obj} *self = ({obj} *){type_obj}.tp_alloc(&{type_obj}, 0);
    if (!self) {{
        Py_DECREF(list);
        return NULL;
    }}
    self->sources = list;
{set_defaults}
    PyObject *kw = kwds; /* borrowed; read-only */
    if (kw) {{
{sum_extracts}
    }}
    return (PyObject *)self;
fail:
    Py_DECREF(self);
    return NULL;
}}
""")

    # getsets: sources (read-only) + each segment scalar.
    getset_fns = [
        f"""static PyObject *
{tname}_get_sources({obj} *self, void *closure)
{{
    (void)closure;
    Py_INCREF(self->sources);
    return self->sources;
}}"""
    ]
    getset_rows = [
        f'    {{"sources", (getter){tname}_get_sources, NULL, NULL, NULL}},'
    ]
    for f in fields:
        n, ct = f["name"], f["type"]
        to_py = _to_py_scalar(ct, f"self->{n}")
        if f.get("_ranged"):
            # scalar, or (lo, hi) when the field's ranged bit is set. Segment
            # ranged fields are integer counts → build/parse via Py_ssize_t.
            flag = f["_ranged"]
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    if (self->ranged & {flag})
        return Py_BuildValue("(nn)", (Py_ssize_t)self->{n},
                             (Py_ssize_t)self->{n}_hi);
    return {to_py};
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    double _lo, _hi;
    int    _r;
    if (!_jm_parse_range(value, &_lo, &_hi, &_r))
        return -1;
    self->{n} = ({ct})_lo;
    if (_r) {{
        self->{n}_hi = ({ct})_hi;
        self->ranged |= {flag};
    }} else {{
        self->ranged &= ~(unsigned){flag};
    }}
    return 0;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )
            continue
        if _field_is_enum(f):
            e = f["enum"]
            # gh-1026: the shared refusal, so a property setter and a
            # constructor argument for the same enum say the same thing.
            _enum_set = _enumc.validate_c(
                n,
                e,
                C.enums(cfg),
                src="s",
                result="_i",
                fail="return -1;",
            )
            getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    return PyUnicode_FromString(_enum_{e}[self->{n}]);
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    const char *s = PyUnicode_AsUTF8(value);
    if (!s) return -1;
{_enum_set}
    self->{n} = _i;
    return 0;
}}""")
            getset_rows.append(
                f'    {{"{n}", (getter){tname}_get_{n}, '
                f"(setter){tname}_set_{n}, NULL, NULL}},"
            )
            continue
        store = f"    self->{n} = {_from_py_scalar(ct, 'value')};"
        getset_fns.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
    return {to_py};
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
{store}
    if (PyErr_Occurred()) return -1;
    return 0;
}}""")
        getset_rows.append(
            f'    {{"{n}", (getter){tname}_get_{n}, '
            f"(setter){tname}_set_{n}, NULL, NULL}},"
        )

    # Feature 4 — flat single-source accessors: a segment built from exactly one
    # source proxies that source's fields as read-only attributes
    # (segment.freq → segment.sources[0].freq), so a project drops a
    # hand-written __getattr__ fallback. Generic — the names come from
    # source.fields and each getter delegates to the source's own getset (so an
    # enum field still reads as a string). A multi-source segment has no single
    # waveform to flatten, so the getter raises AttributeError there.
    for f in _segment_flat_fields(cfg, module):
        n = f["name"]
        getset_fns.append(f"""static PyObject *
{tname}_flat_{n}({obj} *self, void *closure)
{{
    (void)closure;
    if (PyList_GET_SIZE(self->sources) != 1) {{
        PyErr_SetString(PyExc_AttributeError,
                        "{n} is only on a single-source {tname}");
        return NULL;
    }}
    return PyObject_GetAttrString(PyList_GET_ITEM(self->sources, 0), "{n}");
}}""")
        getset_rows.append(
            f'    {{"{n}", (getter){tname}_flat_{n}, NULL, NULL, NULL}},'
        )

    parts.append("\n".join(getset_fns))

    # add(*others) -> Timeline — the time-sequence counterpart of sum (which
    # mixes sources at the same time). Only emitted when the module declares a
    # timeline type.
    add_fn = ""
    add_row = ""
    if tl_tname:
        add_fn = f"""
static PyObject *
{tname}_add({obj} *self, PyObject *args)
{{
    Py_ssize_t n = PyTuple_GET_SIZE(args);
    PyObject *list = PyList_New(n + 1);
    if (!list)
        return NULL;
    Py_INCREF(self);
    PyList_SET_ITEM(list, 0, (PyObject *)self);
    for (Py_ssize_t i = 0; i < n; i++) {{
        PyObject *it = PyTuple_GET_ITEM(args, i);
        Py_INCREF(it);
        PyList_SET_ITEM(list, i + 1, it);
    }}
    PyObject *tl =
        PyObject_CallFunctionObjArgs((PyObject *)&{tl_tname}Type, list, NULL);
    Py_DECREF(list);
    return tl;
}}
"""
        add_row = (
            f'    {{"add", (PyCFunction){tname}_add, METH_VARARGS,\n'
            f'     "add(*others) -> {tl_tname}"}},\n'
        )
    parts.append(add_fn)
    parts.append(f"""
static PyGetSetDef {tname}_getset[] = {{
{chr(10).join(getset_rows)}
    {{NULL, NULL, NULL, NULL, NULL}}
}};

static PyMethodDef {tname}_methods[] = {{
    {{"sum", (PyCFunction)(void (*)(void)){tname}_sum,
     METH_VARARGS | METH_KEYWORDS | METH_CLASS,
     "sum(*sources, **segment_fields) -> {tname}"}},
{add_row}    {{NULL, NULL, 0, NULL}}
}};
""")

    parts.append(f"""static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new       = PyType_GenericNew,
    .tp_init      = (initproc){tname}_init,
    .tp_dealloc   = (destructor){tname}_dealloc,
    .tp_getset    = {tname}_getset,
    .tp_methods   = {tname}_methods,
    .tp_doc       = PyDoc_STR(
        "{tname} — one segment: a list of sources + its span."),
}};
""")
    return "\n".join(parts)


# ── timeline type (e.g. Timeline) ────────────────────────────────────────────


def render_timeline_type(cfg: dict, module: str) -> str:
    """Emit the ``Timeline`` ``PyTypeObject``: an ordered, iterable run of
    segments played back-to-back. A thin sequence wrapper (``add`` / iter /
    ``len`` / subscript) — the fluent face of the segment list the Composer
    already sequences."""
    tl = C.composer_timeline(cfg, module)
    tname = tl.get("type_name", "Timeline")
    pkg = C.project_name(cfg)
    pkg_path = C.capsule_package(cfg, module) or C.module_paths(module).pypath
    dotted = f"{pkg}.{pkg_path.replace('/', '.')}.{tname}"
    obj = f"{tname}Object"
    type_obj = f"{tname}Type"

    return f"""typedef struct {{
    PyObject_HEAD
    PyObject *segments;   /* a Python list */
}} {obj};

static void
{tname}_dealloc({obj} *self)
{{
    Py_XDECREF(self->segments);
    Py_TYPE(self)->tp_free((PyObject *)self);
}}

static int
{tname}_init({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{"segments", NULL}};
    PyObject *seq;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O", kwlist, &seq))
        return -1;
    PyObject *list = PySequence_List(seq); /* copies; accepts any iterable */
    if (!list)
        return -1;
    Py_XSETREF(self->segments, list);
    return 0;
}}

static PyObject *
{tname}_add({obj} *self, PyObject *args)
{{
    Py_ssize_t n = PyTuple_GET_SIZE(args);
    for (Py_ssize_t i = 0; i < n; i++)
        if (PyList_Append(self->segments, PyTuple_GET_ITEM(args, i)) < 0)
            return NULL;
    Py_INCREF(self);
    return (PyObject *)self;
}}

static PyObject *
{tname}_get_segments({obj} *self, void *closure)
{{
    (void)closure;
    Py_INCREF(self->segments);
    return self->segments;
}}

static Py_ssize_t
{tname}_length({obj} *self)
{{
    return PyList_GET_SIZE(self->segments);
}}

static PyObject *
{tname}_subscript({obj} *self, PyObject *key)
{{
    return PyObject_GetItem(self->segments, key);
}}

static PyObject *
{tname}_iter({obj} *self)
{{
    return PyObject_GetIter(self->segments);
}}

static PyMappingMethods {tname}_as_mapping = {{
    .mp_length    = (lenfunc){tname}_length,
    .mp_subscript = (binaryfunc){tname}_subscript,
}};

static PyGetSetDef {tname}_getset[] = {{
    {{"segments", (getter){tname}_get_segments, NULL, NULL, NULL}},
    {{NULL, NULL, NULL, NULL, NULL}}
}};

static PyMethodDef {tname}_methods[] = {{
    {{"add", (PyCFunction){tname}_add, METH_VARARGS,
     "add(*segments) -> {tname}"}},
    {{NULL, NULL, 0, NULL}}
}};

static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new       = PyType_GenericNew,
    .tp_init      = (initproc){tname}_init,
    .tp_dealloc   = (destructor){tname}_dealloc,
    .tp_iter      = (getiterfunc){tname}_iter,
    .tp_as_mapping = &{tname}_as_mapping,
    .tp_getset    = {tname}_getset,
    .tp_methods   = {tname}_methods,
    .tp_doc       = PyDoc_STR("{tname} — segments played back-to-back."),
}};
"""


def render_serializers(
    cfg: dict,
    module: str,
    cname: str,
    obj: str,
    seg_struct: str,
    segments_fn: str,
) -> tuple[str, str]:
    """Emit the ``[[module.X.serializers]]`` delegated serializer methods (gh-317).

    Each is a ``<Composer>.<name>(<params>) -> str`` that coerces its leading
    scalar/enum params (enums validate to their SSOT int), fetches the resolved
    segments, and delegates to the project's C serializer ``fn(<params>, segs,
    n)``. This is the sanctioned path for domain wire formats jm generates none
    of (SigMF / BLUE; see gh-313). Returns ``(funcs, method_rows)``."""
    sers = C.composer_serializers(cfg, module)
    if not sers:
        return "", ""
    funcs: list[str] = []
    rows: list[str] = []
    for s in sers:
        name, fn = s["name"], s["fn"]
        returns = s.get("returns", "str")
        params = list(s.get("params", []))
        decls, kwl, addrs, enum_val, call = [], [], [], [], []
        fmt, barred = "", False
        for p in params:
            pn, pt = p["name"], p["type"]
            kwl.append(f'"{pn}"')
            bar = "|" if (p.get("default") is not None and not barred) else ""
            if bar:
                barred = True
            addrs.append(f"&{pn}")
            if p.get("enum"):
                e = p["enum"]
                decls.append(
                    f'    const char *{pn} = "{p.get("default", "")}";'
                )
                fmt += bar + "s"
                # gh-1026: the shared emitter, with this face's own result
                # spelling. Naming the choices here is the point of the
                # consolidation — it was the only thing separating this
                # refusal from the method-parameter one.
                enum_val.append(
                    _enumc.validate_c(pn, e, C.enums(cfg), result=f"_e_{pn}")
                )
                call.append(f"_e_{pn}")
            else:
                decls.append(f"    {pt} {pn} = {p.get('default', '0')};")
                fmt += bar + _FMT.get(pt, "i")
                call.append(pn)
        decls_s = "\n".join(decls)
        enum_s = ("\n".join(enum_val) + "\n") if enum_val else ""
        call_prefix = (", ".join(call) + ", ") if call else ""
        if params:
            py_sig = "PyObject *args, PyObject *kwds"
            parse = (
                f"    static char *kwlist[] = {{{', '.join(kwl)}, NULL}};\n"
                f"    if (!PyArg_ParseTupleAndKeywords("
                f'args, kwds, "{fmt}", kwlist,\n'
                f"            {', '.join(addrs)}))\n        return NULL;\n"
            )
            flags = "METH_VARARGS | METH_KEYWORDS"
            fnref = f"(PyCFunction)(void (*)(void)){cname}_{name}"
        else:
            py_sig = "PyObject *Py_UNUSED(ignored)"
            parse = ""
            flags = "METH_NOARGS"
            fnref = f"(PyCFunction){cname}_{name}"
        funcs.append(f"""static PyObject *
{cname}_{name}({obj} *self, {py_sig})
{{
    if (self->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError, "composer already closed");
        return NULL;
    }}
{decls_s}
{parse}{enum_s}    size_t _n; int _rep = 0, _cont = 0;
    const {seg_struct} *segs =
        {segments_fn}(self->state, &_n, &_rep, &_cont);
    char *_js = {fn}({call_prefix}segs, _n);
    if (!_js) {{
        PyErr_SetString(PyExc_RuntimeError, "{fn} failed");
        return NULL;
    }}
    PyObject *_s = PyUnicode_FromString(_js);
    free(_js);
    return _s;
}}
""")
        rows.append(
            f'    {{"{name}", {fnref},\n'
            f'     {flags}, "{name}(...) -> {returns}"}},\n'
        )
    return "\n".join(funcs), "".join(rows)


# ── composer type (e.g. Composer) ────────────────────────────────────────────


def render_composer_type(cfg: dict, module: str) -> str:
    """Emit the ``Composer`` ``PyTypeObject`` — the type that drives the backing
    ``wfm_compose_*`` kernel.

    Holds the opaque ``<backing>_state_t *`` directly (the capsule skeleton, as
    a type). ``__init__`` builds a transient ``<segment_struct>[]`` from the OO
    Segment/Synth objects and calls ``<backing>_create`` (which deep-copies,
    incl. each source's ``bits`` — so ownership stays with the Synth objects and
    the transient arrays are freed straight after). ``execute`` returns a
    zero-copy cf32 slice; ``compose`` drains a finite spec; ``segments`` /
    ``repeat`` / ``continuous`` reflect the resolved spec back as OO objects.
    JSON faces (``from_json`` / ``to_json``) land in the next slice."""
    backing = C.capsule_backing(cfg, module)
    src = C.composer_source(cfg, module)
    seg = C.composer_segment(cfg, module)
    oo = C.composer_oo(cfg, module)
    src_t = src["type_name"]
    seg_t = seg["type_name"]
    seg_struct = seg["struct"]
    seg_fields = list(seg.get("fields", []))
    sources_member = seg.get("sources_member", "sources")
    count_member = seg.get("count_member", "n_sources")
    cname = oo.get("composer_type_name", "Composer")
    pkg = C.project_name(cfg)

    # gh-560: when rebuilding source objects out of a resolved segment array,
    # deep-copy EVERY declared bytes field (`<name>` + `n_<name>`) rather than a
    # hardcoded `bits`/`n_bits`. Two bugs in one:
    #   * a source with no bytes field at all did not compile — the block
    #     referenced members that the struct never had, so a composer could only
    #     exist if its source looked like doppler's `wfm_source_t`;
    #   * a source with a bytes field under any other name, or with more than
    #     one (the multi-bytes case `_attach_bytes` already supports), had that
    #     field left ALIASING the composer state's buffer instead of owned, so
    #     the rebuilt object and the state both freed it.
    # Empty when the source declares no bytes field, which is the common case.
    src_bytes_copy = "".join(
        f"""            if (syn->src.{n} && syn->src.n_{n}) {{
                uint8_t *copy = (uint8_t *)malloc(syn->src.n_{n});
                if (copy)
                    memcpy(copy, syn->src.{n}, syn->src.n_{n});
                syn->src.{n} = copy;
            }} else {{
                syn->src.{n} = NULL;
                syn->src.n_{n} = 0;
            }}
"""
        for n in (f["name"] for f in src.get("fields", []) if f.get("bytes"))
    )
    pkg_path = C.capsule_package(cfg, module) or C.module_paths(module).pypath
    dotted = f"{pkg}.{pkg_path.replace('/', '.')}.{cname}"
    obj = f"{cname}Object"
    type_obj = f"{cname}Type"

    create_fn = f"{backing}_create"
    execute_fn = f"{backing}_execute"
    segments_fn = f"{backing}_segments"
    destroy_fn = f"{backing}_destroy"

    # segment scalar field copy lines (both directions). Ranged segment fields
    # also carry their `ranged` bitmask + <name>_hi companion across, so a
    # (lo, hi) draw survives the OO ⇄ struct conversion and from_json round-trip.
    seg_ranged = [f for f in _segment_fields(cfg, module) if f.get("_ranged")]
    fwd_extra = (
        "".join(
            ["\n        segs[i].ranged = seg->ranged;"]
            + [
                f"\n        segs[i].{f['name']}_hi = seg->{f['name']}_hi;"
                for f in seg_ranged
            ]
        )
        if seg_ranged
        else ""
    )
    back_extra = (
        "".join(
            ["\n        sg->ranged = src[i].ranged;"]
            + [
                f"\n        sg->{f['name']}_hi = src[i].{f['name']}_hi;"
                for f in seg_ranged
            ]
        )
        if seg_ranged
        else ""
    )
    fwd = (
        "\n".join(
            f"        segs[i].{f['name']} = seg->{f['name']};"
            for f in seg_fields
        )
        + fwd_extra
    )
    back = (
        "\n".join(
            f"        sg->{f['name']} = src[i].{f['name']};"
            for f in seg_fields
        )
        + back_extra
    )

    # JSON faces (gh-287 C2.3): from_json / from_file classmethods + to_json.
    # from_json/_file follow the regular <backing>_* convention; the serializer
    # is irregular (wfm's is wfm_spec_to_json, not wfm_compose_*), so its name +
    # any trailing literal args (wfm's headroom=0.0) are manifest-declared.
    jtbl = cfg.get("module", {}).get(module, {}).get("json", {})
    json_fns = ""
    json_rows = ""
    # Default: a generic SSOT-driven ser/de generated from the manifest fields
    # (reusable by any composer). `to_json_fn` is an opt-in escape hatch that
    # delegates to a hand-written serializer instead (e.g. wfm byte-compat).
    if C.composer_json(cfg, module) and jtbl.get("to_json_fn"):
        from_json_fn = jtbl.get("from_json_fn", f"{backing}_from_json")
        from_file_fn = jtbl.get("from_file_fn", f"{backing}_from_file")
        to_json_fn = jtbl.get("to_json_fn", f"{backing}_to_json")
        trailing = jtbl.get("to_json_trailing", [])
        trail = "" if not trailing else ", " + ", ".join(trailing)
        json_fns = f"""
static PyObject *
{cname}_from_json(PyObject *cls, PyObject *args)
{{
    PyTypeObject *type = (PyTypeObject *)cls; /* alloc via cls: subclass round-trips */
    const char *json;
    if (!PyArg_ParseTuple(args, "s", &json))
        return NULL;
    {backing}_state_t *st = {from_json_fn}(json);
    if (!st) {{
        PyErr_SetString(PyExc_ValueError, "{from_json_fn} failed");
        return NULL;
    }}
    {obj} *self = ({obj} *)type->tp_alloc(type, 0);
    if (!self) {{
        {destroy_fn}(st);
        return NULL;
    }}
    self->state = st;
    self->destroyed = 0;
    return (PyObject *)self;
}}

static PyObject *
{cname}_from_file(PyObject *cls, PyObject *args)
{{
    PyTypeObject *type = (PyTypeObject *)cls; /* alloc via cls: subclass round-trips */
    PyObject *pathobj;
    if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &pathobj))
        return NULL;
    {backing}_state_t *st = {from_file_fn}(PyBytes_AS_STRING(pathobj));
    Py_DECREF(pathobj);
    if (!st) {{
        PyErr_SetString(PyExc_OSError, "{from_file_fn} failed");
        return NULL;
    }}
    {obj} *self = ({obj} *)type->tp_alloc(type, 0);
    if (!self) {{
        {destroy_fn}(st);
        return NULL;
    }}
    self->state = st;
    self->destroyed = 0;
    return (PyObject *)self;
}}

static PyObject *
{cname}_to_json({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if (self->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError, "composer already closed");
        return NULL;
    }}
    size_t n;
    int repeat = 0, continuous = 0;
    const {seg_struct} *segs =
        {segments_fn}(self->state, &n, &repeat, &continuous);
    char *js = {to_json_fn}(segs, n, repeat, continuous{trail});
    if (!js) {{
        PyErr_SetString(PyExc_RuntimeError, "{to_json_fn} failed");
        return NULL;
    }}
    PyObject *s = PyUnicode_FromString(js);
    free(js);
    return s;
}}
"""
    elif C.composer_json(cfg, module):
        json_fns = render_json_funcs(cfg, module)
    if C.composer_json(cfg, module):
        json_rows = (
            f'    {{"from_json", (PyCFunction){cname}_from_json,\n'
            f"     METH_VARARGS | METH_CLASS,"
            f' "from_json(json) -> {cname}"}},\n'
            f'    {{"from_file", (PyCFunction){cname}_from_file,\n'
            f"     METH_VARARGS | METH_CLASS,"
            f' "from_file(path) -> {cname}"}},\n'
            f'    {{"to_json", (PyCFunction){cname}_to_json, METH_NOARGS,\n'
            f'     "to_json() -> str"}},\n'
        )

    # Feature 3 — a generated stream() iterator (drains execute into blocks),
    # so a project drops its hand-written `for blk in c.stream(n):` wrapper.
    stream_code = stream_row = ""
    if C.composer_stream(cfg, module).get("stream"):
        # gh-317 feature 1: an optional `realtime = fs` that paces the iterator
        # to an fs-Hz clock between blocks, IN the .so — so a project drops its
        # hand-written `paced()` helper over a SampleClock. The clock is opaque
        # (void *), created lazily on the first block, paced by sample count,
        # and destroyed with the iterator. Only emitted when the `realtime`
        # sub-table names the clock create/pace/destroy fns.
        rt = C.composer_stream(cfg, module).get("realtime") or {}
        rt_create = rt.get("clock_create", "")
        rt_pace = rt.get("pace", "")
        rt_destroy = rt.get("destroy", "")
        has_rt = bool(rt_create and rt_pace and rt_destroy)
        rt_fields = "    double realtime;\n    void *clk;\n" if has_rt else ""
        rt_dealloc = (
            f"    if (self->clk)\n        {rt_destroy}(self->clk);\n"
            if has_rt
            else ""
        )
        # gh-343-review #1: the pace sleeps (to the block's ideal deadline), so
        # release the GIL around it — exactly like a `nogil` handle method —
        # or every other Python thread (e.g. a sink consumer) freezes for the
        # full per-block sleep. The clock create touches `self`, so it stays
        # under the GIL; only the blocking pace is wrapped.
        rt_pace_code = (
            f"""    if (self->realtime > 0.0) {{
        if (!self->clk)
            self->clk = {rt_create}(self->realtime, 0);
        Py_BEGIN_ALLOW_THREADS
        {rt_pace}(self->clk, (size_t)n);
        Py_END_ALLOW_THREADS
    }}
"""
            if has_rt
            else ""
        )
        rt_kw = '"block", "realtime", NULL' if has_rt else '"block", NULL'
        rt_decl = "    double realtime = 0.0;\n" if has_rt else ""
        rt_fmt = "|nd" if has_rt else "|n"
        rt_addr = ", &realtime" if has_rt else ""
        rt_init = (
            "    it->realtime = realtime;\n    it->clk = NULL;\n"
            if has_rt
            else ""
        )
        rt_doc = (
            "stream(block=4096, realtime=0.0) -> iterator (realtime=fs paces)"
            if has_rt
            else "stream(block=4096) -> iterator of complex64 blocks"
        )
        stream_code = f"""/* Iterator returned by {cname}.stream(): drains execute() into blocks. */
typedef struct {{
    PyObject_HEAD
    PyObject  *composer; /* strong ref to the {cname} */
    Py_ssize_t block;
{rt_fields}}} {cname}StreamObject;

static PyTypeObject {cname}StreamType;

static void
{cname}Stream_dealloc({cname}StreamObject *self)
{{
{rt_dealloc}    Py_XDECREF(self->composer);
    Py_TYPE(self)->tp_free((PyObject *)self);
}}

static PyObject *
{cname}Stream_iter(PyObject *self)
{{
    Py_INCREF(self);
    return self;
}}

static PyObject *
{cname}Stream_next({cname}StreamObject *self)
{{
    PyObject *blk =
        PyObject_CallMethod(self->composer, "execute", "n", self->block);
    if (!blk)
        return NULL;
    Py_ssize_t n = PyObject_Length(blk);
    if (n < 0) {{ /* error from execute */
        Py_DECREF(blk);
        return NULL;
    }}
    if (n == 0) {{ /* finite spec drained -> StopIteration (NULL, no exception) */
        Py_DECREF(blk);
        return NULL;
    }}
{rt_pace_code}    return blk;
}}

static PyTypeObject {cname}StreamType = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}Stream",
    .tp_basicsize = sizeof({cname}StreamObject),
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_dealloc   = (destructor){cname}Stream_dealloc,
    .tp_iter      = {cname}Stream_iter,
    .tp_iternext  = (iternextfunc){cname}Stream_next,
    .tp_doc       = PyDoc_STR("Iterator over {cname}.stream() blocks."),
}};

static PyObject *
{cname}_stream({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{{rt_kw}}};
    Py_ssize_t block = 4096;
{rt_decl}    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{rt_fmt}", kwlist,
            &block{rt_addr}))
        return NULL;
    if (block <= 0) {{
        PyErr_SetString(PyExc_ValueError, "block must be > 0");
        return NULL;
    }}
    {cname}StreamObject *it =
        PyObject_New({cname}StreamObject, &{cname}StreamType);
    if (!it)
        return NULL;
    Py_INCREF(self);
    it->composer = (PyObject *)self;
    it->block = block;
{rt_init}    return (PyObject *)it;
}}

"""
        stream_row = (
            f'    {{"stream", (PyCFunction)(void (*)(void)){cname}_stream,\n'
            f"     METH_VARARGS | METH_KEYWORDS,\n"
            f'     "{rt_doc}"}},\n'
        )

    # Feature 5 — a generic `to_dict()`: serialize the *resolved* composition
    # (repeat / continuous / segments → [{<seg fields>, sources: [{<src
    # fields>}]}]) into a plain nested Python dict. Driven entirely by the SSOT
    # field names and the OO getsets (so enums render as strings, bits as
    # bytes), it is the generic introspection primitive any sidecar — SigMF,
    # BLUE, a CSV manifest — is built from in Python; jm generates none of those.
    to_dict_code = to_dict_row = ""
    if C.composer_stream(cfg, module).get("to_dict"):
        seg_keys = "".join(
            f'"{f["name"]}", '
            for f in C.composer_segment(cfg, module).get("fields", [])
        )
        src_keys = "".join(
            f'"{f["name"]}", '
            for f in C.composer_source(cfg, module).get("fields", [])
        )
        to_dict_code = f"""/* Serialize one resolved object's named fields into a dict via its getsets
 * (enums → str, bits → bytes) — generic over the SSOT field list. */
static PyObject *
_{cname}_obj_to_dict(PyObject *o, const char *const *keys)
{{
    PyObject *d = PyDict_New();
    if (!d)
        return NULL;
    for (Py_ssize_t i = 0; keys[i]; i++) {{
        PyObject *v = PyObject_GetAttrString(o, keys[i]);
        if (!v) {{ Py_DECREF(d); return NULL; }}
        int rc = PyDict_SetItemString(d, keys[i], v);
        Py_DECREF(v);
        if (rc < 0) {{ Py_DECREF(d); return NULL; }}
    }}
    return d;
}}

static const char *const _{cname}_seg_keys[] = {{ {seg_keys}NULL }};
static const char *const _{cname}_src_keys[] = {{ {src_keys}NULL }};

static PyObject *
{cname}_to_dict({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    PyObject *r = _{cname}_resolved(self);
    if (!r)
        return NULL;
    PyObject *seglist = PyTuple_GET_ITEM(r, 0); /* borrowed */
    Py_ssize_t nseg = PyList_GET_SIZE(seglist);
    PyObject *segs_out = PyList_New(nseg);
    if (!segs_out)
        goto fail;
    for (Py_ssize_t i = 0; i < nseg; i++) {{
        PyObject *seg = PyList_GET_ITEM(seglist, i); /* borrowed */
        PyObject *sd = _{cname}_obj_to_dict(seg, _{cname}_seg_keys);
        if (!sd)
            goto fail_segs;
        PyObject *srcs = PyObject_GetAttrString(seg, "sources");
        if (!srcs) {{ Py_DECREF(sd); goto fail_segs; }}
        Py_ssize_t nsrc = PyList_GET_SIZE(srcs);
        PyObject *src_out = PyList_New(nsrc);
        if (!src_out) {{ Py_DECREF(srcs); Py_DECREF(sd); goto fail_segs; }}
        for (Py_ssize_t k = 0; k < nsrc; k++) {{
            PyObject *kd = _{cname}_obj_to_dict(
                PyList_GET_ITEM(srcs, k), _{cname}_src_keys);
            if (!kd) {{
                Py_DECREF(src_out); Py_DECREF(srcs); Py_DECREF(sd);
                goto fail_segs;
            }}
            PyList_SET_ITEM(src_out, k, kd); /* steals */
        }}
        Py_DECREF(srcs);
        int rc = PyDict_SetItemString(sd, "sources", src_out);
        Py_DECREF(src_out);
        if (rc < 0) {{ Py_DECREF(sd); goto fail_segs; }}
        PyList_SET_ITEM(segs_out, i, sd); /* steals */
    }}
    {{
        PyObject *out = Py_BuildValue(
            "{{s:O,s:O,s:N}}", "repeat", PyTuple_GET_ITEM(r, 1),
            "continuous", PyTuple_GET_ITEM(r, 2), "segments", segs_out);
        Py_DECREF(r);
        return out; /* Py_BuildValue stole segs_out via N */
    }}
fail_segs:
    Py_DECREF(segs_out);
fail:
    Py_DECREF(r);
    return NULL;
}}

"""
        to_dict_row = (
            f'    {{"to_dict", (PyCFunction){cname}_to_dict, METH_NOARGS,\n'
            f'     "to_dict() -> dict (resolved repeat/continuous/segments)"}},\n'
        )

    # gh-317: additional delegated serializers (to_sigmf, …) over the segments.
    serializer_code, serializer_rows = render_serializers(
        cfg, module, cname, obj, seg_struct, segments_fn
    )

    return f"""static PyTypeObject {type_obj}; /* fwd: from_json/from_file alloc */

typedef struct {{
    PyObject_HEAD
    {backing}_state_t *state;
    int                destroyed;
}} {obj};

/* Build a transient {seg_struct}[] from a list of {seg_t} objects. Each
 * source's bits pointer ALIASES the {src_t}'s owned buffer; {create_fn}
 * deep-copies it, so we free only these transient arrays afterwards. */
static {seg_struct} *
_build_{backing}_segments(PyObject *seglist, size_t *n_out)
{{
    Py_ssize_t nseg = PyList_GET_SIZE(seglist);
    {seg_struct} *segs = ({seg_struct} *)calloc((size_t)nseg, sizeof *segs);
    if (!segs) {{
        PyErr_NoMemory();
        return NULL;
    }}
    for (Py_ssize_t i = 0; i < nseg; i++) {{
        {seg_t}Object *seg = ({seg_t}Object *)PyList_GET_ITEM(seglist, i);
        Py_ssize_t ns = PyList_GET_SIZE(seg->sources);
        {src["struct"]} *srcs =
            ({src["struct"]} *)calloc((size_t)(ns ? ns : 1), sizeof *srcs);
        if (!srcs) {{
            for (Py_ssize_t j = 0; j < i; j++)
                free(segs[j].{sources_member});
            free(segs);
            PyErr_NoMemory();
            return NULL;
        }}
        for (Py_ssize_t k = 0; k < ns; k++) {{
            {src_t}Object *syn =
                ({src_t}Object *)PyList_GET_ITEM(seg->sources, k);
            srcs[k] = syn->src; /* scalars + aliased bits ptr */
        }}
        segs[i].{sources_member} = srcs;
        segs[i].{count_member} = (size_t)ns;
{fwd}
    }}
    *n_out = (size_t)nseg;
    return segs;
}}

static void
_free_{backing}_segments({seg_struct} *segs, size_t n)
{{
    if (!segs)
        return;
    for (size_t i = 0; i < n; i++)
        free(segs[i].{sources_member}); /* NOT the bits — owned by {src_t} */
    free(segs);
}}

/* Rebuild a list of {seg_t} objects from a resolved {seg_struct}[] (deep-copies
 * each source's bits so the new {src_t} objects own their own). */
static PyObject *
_{backing}_segments_to_list(const {seg_struct} *src, size_t n)
{{
    PyObject *list = PyList_New((Py_ssize_t)n);
    if (!list)
        return NULL;
    for (size_t i = 0; i < n; i++) {{
        PyObject *srclist = PyList_New((Py_ssize_t)src[i].{count_member});
        if (!srclist) {{
            Py_DECREF(list);
            return NULL;
        }}
        for (size_t k = 0; k < src[i].{count_member}; k++) {{
            {src_t}Object *syn =
                ({src_t}Object *){src_t}Type.tp_alloc(&{src_t}Type, 0);
            if (!syn) {{
                Py_DECREF(srclist);
                Py_DECREF(list);
                return NULL;
            }}
            syn->src = src[i].{sources_member}[k]; /* scalars + bytes ptrs */
            syn->fs = src[i].fs;
{src_bytes_copy}            PyList_SET_ITEM(srclist, (Py_ssize_t)k, (PyObject *)syn);
        }}
        {seg_t}Object *sg =
            ({seg_t}Object *){seg_t}Type.tp_alloc(&{seg_t}Type, 0);
        if (!sg) {{
            Py_DECREF(srclist);
            Py_DECREF(list);
            return NULL;
        }}
        sg->sources = srclist;
{back}
        PyList_SET_ITEM(list, (Py_ssize_t)i, (PyObject *)sg);
    }}
    return list;
}}

static void
{cname}_dealloc({obj} *self)
{{
    if (!self->destroyed && self->state)
        {destroy_fn}(self->state);
    Py_TYPE(self)->tp_free((PyObject *)self);
}}

static int
_pop_flag(PyObject *kw, const char *name, int *out)
{{
    PyObject *o = PyDict_GetItemString(kw, name); /* borrowed */
    if (o) {{
        int v = PyObject_IsTrue(o);
        if (v < 0)
            return -1;
        *out = v;
        if (PyDict_DelItemString(kw, name) < 0)
            return -1;
    }}
    return 0;
}}

static int
{cname}_init({obj} *self, PyObject *args, PyObject *kwds)
{{
    self->state = NULL;
    self->destroyed = 0;
    int repeat = 0, continuous = 0;

    PyObject *kw = kwds ? PyDict_Copy(kwds) : PyDict_New();
    if (!kw)
        return -1;
    if (_pop_flag(kw, "repeat", &repeat) < 0 ||
        _pop_flag(kw, "continuous", &continuous) < 0) {{
        Py_DECREF(kw);
        return -1;
    }}

    PyObject *segments = NULL; /* borrowed */
    if (PyTuple_GET_SIZE(args) >= 1)
        segments = PyTuple_GET_ITEM(args, 0);
    {{
        PyObject *o = PyDict_GetItemString(kw, "segments");
        if (o) {{
            segments = o;
            if (PyDict_DelItemString(kw, "segments") < 0) {{
                Py_DECREF(kw);
                return -1;
            }}
        }}
    }}

    PyObject *seglist = NULL; /* owned list of {seg_t} */
    if (segments == NULL || segments == Py_None) {{
        /* Build a single {seg_t} from the remaining keyword arguments. */
        PyObject *empty = PyTuple_New(0);
        PyObject *one = empty ? PyObject_Call((PyObject *)&{seg_t}Type, empty, kw)
                              : NULL;
        Py_XDECREF(empty);
        Py_DECREF(kw);
        if (!one)
            return -1;
        seglist = PyList_New(1);
        if (!seglist) {{
            Py_DECREF(one);
            return -1;
        }}
        PyList_SET_ITEM(seglist, 0, one); /* steals */
    }} else {{
        if (PyDict_Size(kw) > 0) {{
            PyErr_SetString(PyExc_TypeError,
                "pass either segments or single-segment kwargs, not both");
            Py_DECREF(kw);
            return -1;
        }}
        Py_DECREF(kw);
        if (PyObject_TypeCheck(segments, &{seg_t}Type)) {{
            seglist = PyList_New(1);
            if (!seglist)
                return -1;
            Py_INCREF(segments);
            PyList_SET_ITEM(seglist, 0, segments);
        }} else {{
            seglist = PySequence_List(segments); /* iterates a Timeline/list */
            if (!seglist)
                return -1;
        }}
    }}

    Py_ssize_t nseg = PyList_GET_SIZE(seglist);
    if (nseg < 1) {{
        PyErr_SetString(PyExc_ValueError, "need at least one segment");
        Py_DECREF(seglist);
        return -1;
    }}
    for (Py_ssize_t i = 0; i < nseg; i++) {{
        if (!PyObject_TypeCheck(PyList_GET_ITEM(seglist, i), &{seg_t}Type)) {{
            PyErr_SetString(PyExc_TypeError,
                            "segments must be {seg_t} objects");
            Py_DECREF(seglist);
            return -1;
        }}
    }}

    size_t n;
    {seg_struct} *segs = _build_{backing}_segments(seglist, &n);
    if (!segs) {{
        Py_DECREF(seglist);
        return -1;
    }}
    /* The transient segs' bits pointers ALIAS the Synth objects' buffers, so
     * seglist must outlive {create_fn} (which deep-copies them) — dropping it
     * earlier would, in the single-segment-kwargs path where seglist is the
     * sole owner, free the bits out from under the read. */
    self->state = {create_fn}(segs, n, repeat, continuous);
    _free_{backing}_segments(segs, n);
    Py_DECREF(seglist);
    if (!self->state) {{
        PyErr_SetString(PyExc_ValueError, "{create_fn} failed");
        return -1;
    }}
    return 0;
}}

static PyObject *
{cname}_execute({obj} *self, PyObject *args)
{{
    Py_ssize_t max;
    if (!PyArg_ParseTuple(args, "n", &max))
        return NULL;
    if (self->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError, "composer already closed");
        return NULL;
    }}
    if (max < 0) {{
        PyErr_SetString(PyExc_ValueError, "n must be >= 0");
        return NULL;
    }}
    npy_intp dims[] = {{max}};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_COMPLEX64);
    if (!arr)
        return NULL;
    float _Complex *out =
        (float _Complex *)PyArray_DATA((PyArrayObject *)arr);
    size_t got;
    Py_BEGIN_ALLOW_THREADS
    got = {execute_fn}(self->state, out, (size_t)max);
    Py_END_ALLOW_THREADS
    PyObject *stop = PyLong_FromSsize_t((Py_ssize_t)got);
    PyObject *slice = stop ? PySlice_New(NULL, stop, NULL) : NULL;
    Py_XDECREF(stop);
    PyObject *view = slice ? PyObject_GetItem(arr, slice) : NULL;
    Py_XDECREF(slice);
    Py_DECREF(arr);
    return view;
}}

static PyObject *
{cname}_compose({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{"block", NULL}};
    Py_ssize_t block = 4096;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|n", kwlist, &block))
        return NULL;
    if (self->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError, "composer already closed");
        return NULL;
    }}
    {{
        size_t n;
        int repeat = 0, continuous = 0;
        {segments_fn}(self->state, &n, &repeat, &continuous);
        if (continuous) {{
            PyErr_SetString(PyExc_ValueError,
                "cannot compose() a continuous spec; use execute()");
            return NULL;
        }}
    }}
    PyObject *chunks = PyList_New(0);
    if (!chunks)
        return NULL;
    for (;;) {{
        npy_intp dims[] = {{block}};
        PyObject *arr = PyArray_SimpleNew(1, dims, NPY_COMPLEX64);
        if (!arr) {{
            Py_DECREF(chunks);
            return NULL;
        }}
        float _Complex *out =
            (float _Complex *)PyArray_DATA((PyArrayObject *)arr);
        size_t got;
        Py_BEGIN_ALLOW_THREADS
        got = {execute_fn}(self->state, out, (size_t)block);
        Py_END_ALLOW_THREADS
        if (got == 0) {{
            Py_DECREF(arr);
            break;
        }}
        PyArray_DIMS((PyArrayObject *)arr)[0] = (npy_intp)got; /* trim view */
        if (PyList_Append(chunks, arr) < 0) {{
            Py_DECREF(arr);
            Py_DECREF(chunks);
            return NULL;
        }}
        Py_DECREF(arr);
        if (got < (size_t)block)
            break;
    }}
    PyObject *result;
    if (PyList_GET_SIZE(chunks) == 0) {{
        npy_intp z[] = {{0}};
        result = PyArray_SimpleNew(1, z, NPY_COMPLEX64);
    }} else {{
        result = PyArray_Concatenate(chunks, 0);
    }}
    Py_DECREF(chunks);
    return result;
}}

/* (segments, repeat, continuous) of the resolved spec. */
static PyObject *
_{cname}_resolved({obj} *self)
{{
    if (self->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError, "composer already closed");
        return NULL;
    }}
    size_t n;
    int repeat = 0, continuous = 0;
    const {seg_struct} *segs =
        {segments_fn}(self->state, &n, &repeat, &continuous);
    PyObject *list = _{backing}_segments_to_list(segs, n);
    if (!list)
        return NULL;
    return Py_BuildValue("(NOO)", list, repeat ? Py_True : Py_False,
                         continuous ? Py_True : Py_False);
}}

static PyObject *
{cname}_get_segments({obj} *self, void *closure)
{{
    (void)closure;
    PyObject *r = _{cname}_resolved(self);
    if (!r)
        return NULL;
    PyObject *segs = PyTuple_GET_ITEM(r, 0);
    Py_INCREF(segs);
    Py_DECREF(r);
    return segs;
}}

static PyObject *
{cname}_get_repeat({obj} *self, void *closure)
{{
    (void)closure;
    PyObject *r = _{cname}_resolved(self);
    if (!r)
        return NULL;
    PyObject *v = PyTuple_GET_ITEM(r, 1);
    Py_INCREF(v);
    Py_DECREF(r);
    return v;
}}

static PyObject *
{cname}_get_continuous({obj} *self, void *closure)
{{
    (void)closure;
    PyObject *r = _{cname}_resolved(self);
    if (!r)
        return NULL;
    PyObject *v = PyTuple_GET_ITEM(r, 2);
    Py_INCREF(v);
    Py_DECREF(r);
    return v;
}}

static PyObject *
{cname}_close({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if (!self->destroyed && self->state) {{
        {destroy_fn}(self->state);
        self->destroyed = 1;
    }}
    Py_RETURN_NONE;
}}

static PyObject *
{cname}_enter({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    Py_INCREF(self);
    return (PyObject *)self;
}}

static PyObject *
{cname}_exit({obj} *self, PyObject *args)
{{
    (void)args;
    return {cname}_close(self, NULL);
}}
{json_fns}
static PyGetSetDef {cname}_getset[] = {{
    {{"segments", (getter){cname}_get_segments, NULL, NULL, NULL}},
    {{"repeat", (getter){cname}_get_repeat, NULL, NULL, NULL}},
    {{"continuous", (getter){cname}_get_continuous, NULL, NULL, NULL}},
    {{NULL, NULL, NULL, NULL, NULL}}
}};

{stream_code}{to_dict_code}{serializer_code}static PyMethodDef {cname}_methods[] = {{
    {{"execute", (PyCFunction){cname}_execute, METH_VARARGS,
     "execute(n) -> ndarray[complex64]"}},
    {{"compose", (PyCFunction)(void (*)(void)){cname}_compose,
     METH_VARARGS | METH_KEYWORDS, "compose(block=4096) -> ndarray[complex64]"}},
    {{"close", (PyCFunction){cname}_close, METH_NOARGS, "close() -> None"}},
    {{"__enter__", (PyCFunction){cname}_enter, METH_NOARGS, NULL}},
    {{"__exit__", (PyCFunction){cname}_exit, METH_VARARGS, NULL}},
{stream_row}{to_dict_row}{serializer_rows}{json_rows}    {{NULL, NULL, 0, NULL}}
}};

static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new       = PyType_GenericNew,
    .tp_init      = (initproc){cname}_init,
    .tp_dealloc   = (destructor){cname}_dealloc,
    .tp_getset    = {cname}_getset,
    .tp_methods   = {cname}_methods,
    .tp_doc       = PyDoc_STR("{cname} — multi-segment composer."),
}};
"""


# ── whole-module assembly + materialization ──────────────────────────────────


def _type_names(cfg: dict, module: str) -> list[str]:
    """The OO type names the module registers, in definition order."""
    names = [
        C.composer_source(cfg, module)["type_name"],
        C.composer_segment(cfg, module)["type_name"],
    ]
    tl = C.composer_timeline(cfg, module).get("type_name")
    if tl:
        names.append(tl)
    names.append(
        C.composer_oo(cfg, module).get("composer_type_name", "Composer")
    )
    return names


def render_ext(cfg: dict, module: str) -> str:
    """Assemble the full ``<module>_ext.c`` for a composer module (gh-287).

    Concatenates the enum tables and the source / segment / timeline / composer
    types, then emits the module method table (the source factories) and the
    ``PyInit`` that readies and registers every type. The DSP kernels stay in
    the backing ``_core.c``; this file is pure generated glue."""
    backing = C.capsule_backing(cfg, module)
    header = C.capsule_header(cfg, module) or f"{backing}/{backing}_core.h"
    mp = C.module_paths(module)
    leaf = mp.leaf

    # The generic generated JSON path pulls in cJSON + stdio (file read); the
    # delegation path (json.to_json_fn) needs neither here.
    jtbl = cfg.get("module", {}).get(module, {}).get("json", {})
    gen_json = C.composer_json(cfg, module) and not jtbl.get("to_json_fn")
    json_header = jtbl.get("header", "cJSON.h")
    json_includes = (
        f'#include <stdio.h>\n#include "{json_header}"\n' if gen_json else ""
    )

    # Standalone generation pulls in the composed generator's header and the
    # project's straight-C bridge declaration (source config -> generator state).
    # gh-317: the realtime stream's clock fns need their header.
    _rt = C.composer_stream(cfg, module).get("realtime") or {}
    rt_includes = f'#include "{_rt["header"]}"\n' if _rt.get("header") else ""

    # gh-343: a delegated serializer's C fn lives in an arbitrary project header
    # (e.g. wfm/wfm_writer.h), not an auto-included <dep>_core.h — so emit each
    # serializer's optional `header`, or its call is an implicit declaration
    # that miscompiles (a str fn read as int). Deduped, in declaration order.
    _ser_headers: list[str] = []
    for _s in C.composer_serializers(cfg, module):
        _h = _s.get("header")
        if _h and _h not in _ser_headers:
            _ser_headers.append(_h)
    serializer_includes = "".join(f'#include "{h}"\n' for h in _ser_headers)

    gen = _source_generates(cfg, module)
    gen_includes = f'#include "{gen["header"]}"\n' if gen else ""

    # gh-998: the bridge and the computed-property fns are the project's own
    # straight C, and used to be declared right here as `extern` lines — the
    # only place their signatures existed, so a C test or benchmark could
    # reach them only by writing a second copy. They are published in
    # `<cname>_bridge.h` now and included, which is what makes the count one.
    if render_bridge_h(cfg, module):
        gen_includes += f'#include "{mp.cname}/{mp.cname}_bridge.h"\n'

    parts = [
        f"""/*
 * {mp.cname}_ext.c — composer extension for `{backing}` (generated by jm; gh-287).
 *
 * The Synth / Segment / Timeline / Composer OO types live here, in the .so;
 * the composition kernels stay hand-written in the backing _core.c.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>
#include <stdlib.h>
#include <string.h>

#include "{header}"
{gen_includes}{json_includes}{rt_includes}{serializer_includes}""",
        render_enum_tables(cfg, module),
        render_range_helper(cfg, module),
        render_source_type(cfg, module),
        render_segment_type(cfg, module),
    ]
    if C.composer_timeline(cfg, module).get("type_name"):
        parts.append(render_timeline_type(cfg, module))
    parts.append(render_composer_type(cfg, module))

    # module method table — the source factories.
    rows = factory_method_rows(cfg, module)
    table = "\n".join(rows)
    parts.append(f"""static PyMethodDef _methods[] = {{
{table}
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef _moduledef = {{
    PyModuleDef_HEAD_INIT, "{leaf}", NULL, -1, _methods,
    NULL, NULL, NULL, NULL
}};
""")

    types = _type_names(cfg, module)
    ready = "\n".join(
        f"    if (PyType_Ready(&{t}Type) < 0) return NULL;" for t in types
    )
    # The stream() iterator is an internal type — readied but not module-exposed.
    if C.composer_stream(cfg, module).get("stream"):
        cn = C.composer_oo(cfg, module).get("composer_type_name", "Composer")
        ready += f"\n    if (PyType_Ready(&{cn}StreamType) < 0) return NULL;"
    add = "\n".join(
        f"    Py_INCREF(&{t}Type);\n"
        f'    PyModule_AddObject(m, "{t}", (PyObject *)&{t}Type);'
        for t in types
    )
    parts.append(f"""PyMODINIT_FUNC
PyInit_{leaf}(void)
{{
    import_array();
{ready}
    PyObject *m = PyModule_Create(&_moduledef);
    if (!m) return NULL;
{add}
    return m;
}}
""")
    return "\n".join(parts)


def render_cmake(cfg: dict, module: str) -> str:
    """Render the composer module's ``CMakeLists.txt`` — one Python-extension
    target linking the ``link = true`` dependency cores + ``extra_link_libs``,
    dropping the ``.so`` into the (optionally overridden) package directory.
    Mirrors the capsule CMake generator."""
    mp = C.module_paths(module)
    leaf, cname = mp.leaf, mp.cname
    out_pkg = C.capsule_package(cfg, module) or mp.pypath
    link_cores = C.dep_link_libs(C.capsule_depends_on(cfg, module))
    extra = C.capsule_extra_link_libs(cfg, module)
    link_lines = "".join(f"    {lib}\n" for lib in link_cores + extra)
    # The generic generated JSON path needs the project's cJSON header on the
    # include path (declared via json.include_dir; delegation needs nothing).
    jtbl = cfg.get("module", {}).get(module, {}).get("json", {})
    gen_json = C.composer_json(cfg, module) and not jtbl.get("to_json_fn")
    inc = " ${CMAKE_SOURCE_DIR}/native/inc"
    if gen_json and jtbl.get("include_dir"):
        inc += f"\n    {jtbl['include_dir']}"
    return f"""if(BUILD_PYTHON)

# {cname} — composer extension for `{C.capsule_backing(cfg, module)}` (gh-287).
# The OO types live in the .so; kernels are in the backing _core.c.
# Generated by just-makeit from [module.{module}] — edit the manifest, not this.
Python3_add_library({leaf} MODULE WITH_SOABI {cname}_ext.c)
target_link_libraries({leaf} PRIVATE
{link_lines}    Python3::NumPy)
target_include_directories({leaf} PRIVATE{inc})
set_target_properties({leaf} PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${{PYTHON_PACKAGE_DIR}}/{out_pkg}"
    RUNTIME_OUTPUT_DIRECTORY "${{PYTHON_PACKAGE_DIR}}/{out_pkg}")
add_custom_command(TARGET {leaf} POST_BUILD
    COMMAND ${{CMAKE_COMMAND}} -E copy_if_different
        "$<TARGET_FILE:{leaf}>"
        "${{PYTHON_PACKAGE_DIR}}/{out_pkg}/$<TARGET_FILE_NAME:{leaf}>"
    VERBATIM
    COMMENT "Copy {leaf} extension module")

endif()
{_cli_cmake_block(cfg, module, link_cores, extra)}"""


def _cli_cmake_block(
    cfg: dict, module: str, link_cores: list, extra: list
) -> str:
    """CMake for the optional standalone c-face CLI (a pure-C tool — no Python,
    so it sits outside the BUILD_PYTHON guard)."""
    cli = composer_cli(cfg, module)
    if not cli.get("enabled"):
        return ""
    mp = C.module_paths(module)
    prog = cli.get("name", module)
    libs = "".join(f"    {lib}\n" for lib in link_cores + extra)
    return f"""
# {prog} — generated c-face composer CLI (gh-287). Pure C; no Python.
add_executable({prog} {mp.cname}_cli.c)
target_link_libraries({prog} PRIVATE
{libs})
target_include_directories({prog} PRIVATE ${{CMAKE_SOURCE_DIR}}/native/inc)
"""


def _field_is_numeric(f: dict) -> bool:
    """A source/segment field whose value is numeric (a scalar ``float``/``int``
    or a ranged ``… | tuple`` of them) — i.e. anything that is not an enum
    string, a ``bytes`` buffer, or a ``complex`` stream. Used to decide whether
    a docstring default is rendered bare (``0.0``) or quoted (``"tone"``)."""
    return not f.get("enum") and not f.get("bytes") and not f.get("complex")


def _pyi_field_type(f: dict) -> str:
    """The ``.pyi`` annotation type for a source/segment field."""
    if f.get("enum"):
        return "str"
    if f.get("bytes"):
        return "bytes | None"
    if f.get("complex"):
        return "NDArray[np.complex64] | None"
    scalar = "float" if f["type"] in ("double", "float") else "int"
    if f.get("_ranged"):  # scalar, or a (lo, hi) per-repeat uniform draw
        return f"{scalar} | tuple[{scalar}, {scalar}]"
    return scalar


def _pyi_field_sig(fields: list[dict]) -> str:
    """Keyword signature fragment for a source/segment field list."""
    return ", ".join(
        f"{f['name']}: {_pyi_field_type(f)} = ..." for f in fields
    )


def _pyi_doc_lines(
    type_name: str,
    fields: list[dict],
    enum_reg: dict[str, list[str]],
) -> list[str]:
    """4-space-indented numpy-style class docstring from a field list (gh-375).

    gh-747: layout, wrapping and delimiters are `_docstring.class_docstring`'s;
    this function's job is deriving each field's type line and notes.
    """
    params: list[ClassParam] = []
    for f in fields:
        ann = _pyi_field_type(f)
        type_line = f"{f['name']} : {ann}"
        if "default" in f:
            dv = f["default"]
            # A numeric field (incl. a ranged ``float | tuple[float, float]``)
            # renders its default bare; only string/enum defaults are quoted.
            if _field_is_numeric(f):
                type_line += f", default {dv}"
            else:
                type_line += f', default ``"{dv}"``'
        elif f.get("bytes") or f.get("complex"):
            type_line += ", default None"
        # Optional per-field description (manifest ``doc =``), then — for an
        # enum field — its choice list. Both are wrapped by the shared builder
        # at CLASS_DESC_WIDTH; gh-744's measured case was a 695-column
        # manifest `doc =` on doppler's `background` field.
        notes: list[str] = []
        if f.get("doc"):
            notes.append(f["doc"])
        if f.get("enum"):
            choices = enum_reg.get(f["enum"], [])
            if choices:
                choice_str = ", ".join(f'``"{c}"``' for c in choices)
                notes.append(f"One of {choice_str}.")
        params.append(ClassParam(type_line, tuple(notes)))
    return class_docstring(f"{type_name}.", params=params)


def render_pyi(cfg: dict, module: str) -> str:
    """Render a typed ``.pyi`` stub for the composer's OO surface.

    Covers the source / segment / timeline / composer types, the factories,
    and — when enabled — the JSON faces. Docstrings include defaults and enum
    choices from the manifest (gh-375)."""
    enum_reg = C.enums(cfg)
    src = C.composer_source(cfg, module)
    seg = C.composer_segment(cfg, module)
    oo = C.composer_oo(cfg, module)
    src_t = src["type_name"]
    seg_t = seg["type_name"]
    tl_t = C.composer_timeline(cfg, module).get("type_name")
    cname = oo.get("composer_type_name", "Composer")
    # Annotated so ranged fields render `float | tuple[float, float]`.
    src_fields = _source_fields(cfg, module)
    seg_fields = _segment_fields(cfg, module)
    src_sig = _pyi_field_sig(src_fields)
    seg_scalar_sig = _pyi_field_sig(seg_fields)
    has_stream = bool(C.composer_stream(cfg, module).get("stream"))
    # gh-560: a Timeline's `__iter__` is annotated too, so Iterator is needed
    # whenever a timeline exists — not only for the composer's stream().
    typing_imports = "Any, Iterator" if (has_stream or tl_t) else "Any"

    # The fs segment field also appears at the end of Synth.__init__.
    fs_field = next((f for f in seg_fields if f.get("name") == "fs"), None)
    synth_doc_fields = src_fields + ([fs_field] if fs_field else [])

    lines = [
        f"# {C.module_paths(module).leaf}.pyi — composer OO types (jm; gh-287).",
        "from __future__ import annotations",
        f"from typing import {typing_imports}",
        # gh-560: every composer type is a C type with its own instance layout,
        # so it is a *disjoint base* — it cannot be combined with another such
        # base by multiple inheritance. Unlike the handle and object kinds,
        # these are Py_TPFLAGS_BASETYPE (subclassing them is a shipped feature,
        # 0.19.17), so `@final` would be a lie; `@disjoint_base` is the accurate
        # marker. It lives in typing_extensions, whose stubs mypy bundles — a
        # `.pyi` is never executed, so this adds no runtime dependency.
        "from typing_extensions import disjoint_base",
        "import numpy as np",
        "from numpy.typing import NDArray",
        "",
        "@disjoint_base",
        f"class {src_t}:",
    ]
    lines.extend(_pyi_doc_lines(src_t, synth_doc_fields, enum_reg))
    lines += [
        f"    def __init__(self, {src_sig}{', ' if src_sig else ''}"
        "fs: float = ...) -> None: ...",
    ]
    # gh-560: the declared fields are real read/write getsets on the type, so
    # they belong in the stub. They were missing, and a blanket
    # `def __getattr__(self, name: str) -> Any` stood in their place — which
    # the runtime never had (no tp_getattro is emitted; fields are tp_getset).
    # That hatch told a type checker every attribute exists, which is exactly
    # what hid the omission: `synth.freq` checked fine for the wrong reason,
    # and so did `synth.frq`.
    lines += [f"    {f['name']}: {_pyi_field_type(f)}" for f in src_fields]
    lines.append("    fs: float")
    if _source_generates(cfg, module):
        lines += [
            "    def steps(self, n: int) -> NDArray[np.complex64]:",
            '        """Generate *n* complex samples."""',
            "    def step(self) -> complex:",
            '        """Generate one complex sample."""',
            "    def reset(self) -> None:",
            '        """Reset to initial state."""',
        ]
    # Feature 6 — computed read-only properties (derived in C; never stale).
    for c in _source_computed(cfg, module):
        pytype = "float" if c["type"] in ("double", "float") else "int"
        lines.append(f"    {c['name']}: {pytype}")
    lines += ["", "@disjoint_base", f"class {seg_t}:"]
    lines.extend(_pyi_doc_lines(seg_t, src_fields + seg_fields, enum_reg))
    lines += [
        f"    sources: list[{src_t}]",
        # gh-560: the segment's own scalar fields are getsets too (same
        # omission as the source's above).
        *[f"    {f['name']}: {_pyi_field_type(f)}" for f in seg_fields],
        # Feature 4 — flat single-source accessors (read-only; AttributeError on
        # a multi-source segment).
        *[
            f"    {f['name']}: {_pyi_field_type(f)}"
            for f in _segment_flat_fields(cfg, module)
        ],
        f"    def __init__(self, {src_sig}{', ' if src_sig else ''}"
        f"{seg_scalar_sig}) -> None: ...",
        "    @classmethod",
        f"    def sum(cls, *sources: {src_t}, {seg_scalar_sig}) -> {seg_t}:",
        f'        """Combine *sources* into a single {seg_t}."""',
    ]
    if tl_t:
        lines += [
            f"    def add(self, *others: {seg_t}) -> {tl_t}:",
            f'        """Append segments; return a {tl_t}."""',
            "",
            "@disjoint_base",
            f"class {tl_t}:",
            f'    """{tl_t}."""',
            f"    segments: list[{seg_t}]",
            f"    def __init__(self, segments: list[{seg_t}]) -> None: ...",
            f"    def add(self, *segments: {seg_t}) -> {tl_t}:",
            '        """Append and return self."""',
            f"    def __iter__(self) -> Iterator[{seg_t}]: ...",
            "    def __len__(self) -> int: ...",
            # gh-560: `/` — a tp_as_sequence slot takes its index positionally
            # and cannot be called by keyword, so the stub must say so.
            f"    def __getitem__(self, i: int, /) -> {seg_t}: ...",
        ]
    seg_or_tl = (
        f"{seg_t} | {tl_t} | list[{seg_t}] | None"
        if tl_t
        else (f"{seg_t} | list[{seg_t}] | None")
    )
    # gh-747: the composer's own class docstring was a fourth hand-written
    # copy of this layout. Its strings are short, but `seg_or_tl` interpolates
    # the project's type names — long ones overflow the `segments :` line, and
    # `reflow_pyi` reflows signatures, not docstring type lines.
    lines += [
        "",
        "@disjoint_base",
        f"class {cname}:",
        *class_docstring(
            f"{cname}.",
            params=[
                ClassParam(
                    f"segments : {seg_or_tl}, default None",
                    ("Initial segment list.",),
                ),
                ClassParam(
                    "repeat : bool, default False",
                    ("Loop the sequence after the last segment.",),
                ),
                ClassParam(
                    "continuous : bool, default False",
                    (
                        "Never finish; execute always returns the "
                        "requested count.",
                    ),
                ),
            ],
        ),
        f"    segments: list[{seg_t}]",
        "    repeat: bool",
        "    continuous: bool",
        f"    def __init__(self, segments: {seg_or_tl} = ..., *, "
        "repeat: bool = ..., continuous: bool = ..., **segment_kwargs"
        ") -> None: ...",
        "    def execute(self, n: int) -> NDArray[np.complex64]:",
        '        """Execute for *n* samples."""',
        "    def compose(self, block: int = ...) -> NDArray[np.complex64]:",
        '        """Compose the full sequence into one array."""',
    ]
    if C.composer_stream(cfg, module).get("stream"):
        rt_arg = (
            ", realtime: float = ..."
            if C.composer_stream(cfg, module).get("realtime")
            else ""
        )
        lines += [
            f"    def stream(self, block: int = ...{rt_arg})"
            " -> Iterator[NDArray[np.complex64]]:",
            '        """Iterate the sequence in blocks."""',
        ]
    if C.composer_stream(cfg, module).get("to_dict"):
        lines += [
            "    def to_dict(self) -> dict:",
            '        """Serialise the composer state to a dict."""',
        ]
    for s in C.composer_serializers(cfg, module):
        sig = "".join(
            f", {p['name']}: {_pyi_field_type(p)} = ..."
            for p in s.get("params", [])
        )
        ret = s.get("returns", "str")
        lines += [
            f"    def {s['name']}(self{sig}) -> {ret}:",
            f'        """Serialise as {s["name"]}."""',
        ]
    lines += [
        "    def close(self) -> None:",
        '        """Release native resources."""',
        f"    def __enter__(self) -> {cname}: ...",
        "    def __exit__(self, *exc) -> None: ...",
    ]
    if C.composer_json(cfg, module):
        lines += [
            "    @classmethod",
            f"    def from_json(cls, json: str) -> {cname}: ...",
            "    @classmethod",
            f"    def from_file(cls, path: str) -> {cname}: ...",
            "    def to_json(self) -> str: ...",
        ]
    lines.append("")
    for fac in oo.get("factories", []):
        lines += [
            f"def {fac}(**kw: Any) -> {src_t}:",
            f'    """Return a {src_t} configured as a *{fac}* source."""',
        ]
    lines.append("")
    # gh-744: the third stub producer, reflowed like the other two
    # (`_render.render_component_pyi`, `_stubs.make_module_pyi`).
    return reflow_pyi("\n".join(lines))


def render_bridge_h(cfg: dict, module: str) -> str:
    """The public header for a composer's project-written straight-C seams.

    gh-998. A composer source can hand two kinds of work back to the project as
    plain C, with no CPython in sight: ``[module.X.source.generates] bridge_fn``
    builds the composed generator from the source struct, and each
    ``[[module.X.source.computed]] fn`` derives a read-only property from it.
    jm knows both signatures exactly — the manifest names the function, and the
    return type and parameter list are derived — and it *wrote* them down, but
    only as ``extern`` lines inside the generated ``_ext.c``. No other
    translation unit could see them.

    That is the shape this repo has been burned by before: a C consumer's only
    route to a declaration jm owns is to write a second copy of it. The cost
    landed on a doppler test asserting that a composer's build path and the
    standalone one agree — it could reach only the half with a public header,
    and had to say in a comment that the other half is covered from Python
    instead.

    So the same prototypes are published here, once, and ``_ext.c`` includes
    this rather than re-declaring them. The *definitions* are untouched: they
    stay the project's hand-written straight C, which is the right split. This
    is only about where the signature is written down.

    Returns ``""`` when the source declares neither seam — the common case, and
    the reason :func:`materialize` writes no file for it.
    """
    gen = _source_generates(cfg, module)
    computed = _source_computed(cfg, module)
    if not gen and not computed:
        return ""

    backing = C.capsule_backing(cfg, module)
    header = C.capsule_header(cfg, module) or f"{backing}/{backing}_core.h"
    src_struct = C.composer_source(cfg, module)["struct"]
    mp = C.module_paths(module)
    guard = f"{mp.cname.upper()}_BRIDGE_H"

    # The struct every prototype takes, and (for the bridge) the generator
    # state it returns. Carried here rather than left to the includer: a
    # header a consumer must prepare for is one they will get wrong once.
    includes = [f'#include "{header}"']
    if gen and gen["header"] != header:
        includes.append(f'#include "{gen["header"]}"')

    lines = [
        "/*",
        f" * {mp.cname}_bridge.h — straight-C seams of the `{module}`"
        " composer (generated by jm; gh-998).",
        " *",
        " * Every function declared here is written by THIS PROJECT, in plain"
        " C, and",
        " * called by the generated binding. The declarations are jm's so that"
        " there",
        " * is exactly one of each: include this header from a test, a"
        " benchmark or a",
        " * sibling component rather than re-declaring a signature jm already"
        " owns.",
        " */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        *includes,
        "",
        "#ifdef __cplusplus",
        'extern "C" {',
        "#endif",
        "",
    ]
    if gen:
        lines += [
            "/* Build the composed generator from a source config"
            " (source -> generator). */",
            f"{gen['state_type']} *{gen['bridge_fn']}("
            f"const {src_struct} *, double);",
            "",
        ]
    for c in computed:
        lines += [
            f"/* Computed read-only property `{c['name']}`. */",
            f"{c['type']} {c['fn']}(const {src_struct} *);",
            "",
        ]
    lines += [
        "#ifdef __cplusplus",
        "}",
        "#endif",
        "",
        f"#endif /* {guard} */",
        "",
    ]
    return "\n".join(lines)


def materialize(cfg: dict, root: Path, module: str) -> None:
    """Write a composer module's generated files into *root* and wire the top
    ``CMakeLists.txt`` ``add_subdirectory``. Mirrors the capsule materializer:
    the binding / CMake / ``.pyi`` are glue the apply pass syncs onto the real
    project."""
    from ._init import _write

    pkg = C.project_name(cfg)
    mp = C.module_paths(module)
    out_pkg = C.capsule_package(cfg, module) or mp.pypath

    # gh-998: the project's straight-C seams, published before the binding
    # that calls them. Absent when the source declares none, so a composer
    # without a bridge or a computed property gains no file.
    _bridge_h = render_bridge_h(cfg, module)
    if _bridge_h:
        _write(
            root / "native" / "inc" / mp.cname / f"{mp.cname}_bridge.h",
            _bridge_h,
        )
    _write(
        root / "native" / "src" / mp.cname / f"{mp.cname}_ext.c",
        render_ext(cfg, module),
    )
    _write(
        root / "native" / "src" / mp.cname / "CMakeLists.txt",
        render_cmake(cfg, module),
    )
    _write(
        root / "src" / pkg / out_pkg / f"{mp.leaf}.pyi",
        render_pyi(cfg, module),
    )
    if composer_cli(cfg, module).get("enabled"):
        _write(
            root / "native" / "src" / mp.cname / f"{mp.cname}_cli.c",
            render_cli(cfg, module),
        )

    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        text = cmake_path.read_text(encoding="utf-8")
        sub = f"add_subdirectory(native/src/{mp.cname})\n"
        if sub not in text:
            sentinel = "# ── Modules"
            if sentinel in text:
                idx = text.index(sentinel)
                idx = text.index("\n", idx) + 1
                text = text[:idx] + sub + text[idx:]
            else:
                text += sub
            cmake_path.write_text(text, encoding="utf-8")


# ── generic JSON ser/de from the [[enum]] SSOT (gh-287) ──────────────────────


def _default_literal(f: dict) -> str:
    """A C double literal for a field's parse fallback (numeric fields)."""
    d = f.get("default", "")
    return d if d not in (None, "") else "0"


def _ser_ranged(jobj: str, cobj: str, name: str, flag: str) -> str:
    """Generic-JSON serialize for a ranged field: a [lo, hi] array when the
    field's ranged bit is set, else a plain number."""
    return (
        f"        if ({cobj}->ranged & {flag}) {{\n"
        f'            cJSON *_r = cJSON_AddArrayToObject({jobj}, "{name}");\n'
        f"            cJSON_AddItemToArray(_r,"
        f" cJSON_CreateNumber((double){cobj}->{name}));\n"
        f"            cJSON_AddItemToArray(_r,"
        f" cJSON_CreateNumber((double){cobj}->{name}_hi));\n"
        f"        }} else {{\n"
        f'            cJSON_AddNumberToObject({jobj}, "{name}",'
        f" (double){cobj}->{name});\n"
        f"        }}"
    )


def _parse_ranged(
    jobj: str, cobj: str, name: str, ct: str, flag: str, default: str
) -> str:
    """Generic-JSON parse for a ranged field: a two-element [lo, hi] array sets
    the ranged bit + companion; a scalar (or absence) is the constant."""
    return (
        f"        {{\n"
        f"            const cJSON *_it ="
        f' cJSON_GetObjectItemCaseSensitive({jobj}, "{name}");\n'
        f"            if (cJSON_IsArray(_it)"
        f" && cJSON_GetArraySize(_it) == 2) {{\n"
        f"                {cobj}->{name} = ({ct})cJSON_GetNumberValue("
        f"cJSON_GetArrayItem(_it, 0));\n"
        f"                {cobj}->{name}_hi = ({ct})cJSON_GetNumberValue("
        f"cJSON_GetArrayItem(_it, 1));\n"
        f"                {cobj}->ranged |= {flag};\n"
        f"            }} else {{\n"
        f'                {cobj}->{name} = ({ct})_json_num({jobj}, "{name}",'
        f" {default});\n"
        f"            }}\n"
        f"        }}"
    )


def render_json_funcs(cfg: dict, module: str) -> str:
    """Emit a GENERIC, SSOT-driven ``to_json`` / ``from_json`` / ``from_file``
    for a composer — no hand-written wire schema, reusable by any composer
    (e.g. a future ddc composer), not just wfm.

    The schema is uniform and derived entirely from the manifest: every segment
    is ``{<segment scalar fields>, "sources": [ {<source fields>}, … ]}``; enum
    fields serialize as their SSOT string (via the generated ``_enum_*`` tables —
    one definition, no duplicated table), a ``bytes`` field as a JSON int array,
    everything else as a number. Round-trips by construction. Uses cJSON for
    robust parsing/formatting (the project links its json lib via
    ``extra_link_libs`` and exposes ``cJSON.h``)."""
    backing = C.capsule_backing(cfg, module)
    src = C.composer_source(cfg, module)
    seg = C.composer_segment(cfg, module)
    oo = C.composer_oo(cfg, module)
    seg_struct = seg["struct"]
    src_struct = src["struct"]
    seg_fields = _segment_fields(cfg, module)
    src_fields = _source_fields(cfg, module)
    sources_member = seg.get("sources_member", "sources")
    count_member = seg.get("count_member", "n_sources")
    cname = oo.get("composer_type_name", "Composer")
    obj = f"{cname}Object"
    create_fn = f"{backing}_create"
    segments_fn = f"{backing}_segments"
    destroy_fn = f"{backing}_destroy"

    # ── to_json: serialize one source object ──
    src_ser = []
    for f in src_fields:
        n = f["name"]
        if f.get("complex"):
            continue  # complex streams aren't generic-JSON serializable
        if f.get("enum"):
            e = f["enum"]
            src_ser.append(
                f'        cJSON_AddStringToObject(so, "{n}", '
                f"_enum_{e}[src->{n}]);"
            )
        elif f.get("bytes"):
            src_ser.append(f"""        if (src->bits && src->n_bits) {{
            cJSON *ba = cJSON_AddArrayToObject(so, "{n}");
            for (size_t bi = 0; bi < src->n_bits; bi++)
                cJSON_AddItemToArray(ba, cJSON_CreateNumber(src->bits[bi]));
        }}""")
        elif f.get("_ranged"):
            src_ser.append(_ser_ranged("so", "src", n, f["_ranged"]))
        else:
            src_ser.append(
                f'        cJSON_AddNumberToObject(so, "{n}", '
                f"(double)src->{n});"
            )
    src_ser_s = "\n".join(src_ser)

    seg_ser = "\n".join(
        _ser_ranged("sj", "g", f["name"], f["_ranged"])
        if f.get("_ranged")
        else (
            f'        cJSON_AddStringToObject(sj, "{f["name"]}", '
            f"_enum_{f['enum']}[g->{f['name']}]);"
            if f.get("enum")
            else f'        cJSON_AddNumberToObject(sj, "{f["name"]}", '
            f"(double)g->{f['name']});"
        )
        for f in seg_fields
    )

    # ── from_json: parse one source object into *src ──
    src_parse = []
    for f in src_fields:
        n = f["name"]
        if f.get("complex"):
            continue  # complex streams aren't generic-JSON serializable
        if f.get("enum"):
            e = f["enum"]
            src_parse.append(f"""        {{
            const char *_s = cJSON_GetStringValue(
                cJSON_GetObjectItemCaseSensitive(so, "{n}"));
            int _v = _enum_index(_enum_{e}, _s ? _s : "{f.get("default", "")}");
            src->{n} = _v < 0 ? 0 : _v;
        }}""")
        elif f.get("bytes"):
            src_parse.append(f"""        {{
            const cJSON *_b = cJSON_GetObjectItemCaseSensitive(so, "{n}");
            if (cJSON_IsArray(_b) && cJSON_GetArraySize(_b) > 0) {{
                size_t _nb = (size_t)cJSON_GetArraySize(_b);
                uint8_t *_buf = (uint8_t *)malloc(_nb);
                if (!_buf) return -1;
                size_t _k = 0;
                const cJSON *_e = NULL;
                cJSON_ArrayForEach(_e, _b)
                    _buf[_k++] = (uint8_t)cJSON_GetNumberValue(_e);
                src->bits = _buf;
                src->n_bits = _nb;
            }}
        }}""")
        elif f.get("_ranged"):
            src_parse.append(
                _parse_ranged(
                    "so",
                    "src",
                    n,
                    f["type"],
                    f["_ranged"],
                    _default_literal(f),
                )
            )
        else:
            ct = f["type"]
            src_parse.append(
                f'        src->{n} = ({ct})_json_num(so, "{n}", '
                f"{_default_literal(f)});"
            )
    src_parse_s = "\n".join(src_parse)

    seg_parse = "\n".join(
        _parse_ranged(
            "sj", "sg", f["name"], f["type"], f["_ranged"], _default_literal(f)
        )
        if f.get("_ranged")
        else (
            f"""        {{
            const char *_s = cJSON_GetStringValue(
                cJSON_GetObjectItemCaseSensitive(sj, "{f["name"]}"));
            int _v = _enum_index(_enum_{f["enum"]},
                                 _s ? _s : "{f.get("default", "")}");
            sg->{f["name"]} = _v < 0 ? 0 : _v;
        }}"""
            if f.get("enum")
            else f"        sg->{f['name']} = ({f['type']})_json_num(sj, "
            f'"{f["name"]}", {_default_literal(f)});'
        )
        for f in seg_fields
    )

    return f"""/* ── generic SSOT-driven JSON (de)serialization ── */
static double
_json_num(const cJSON *o, const char *key, double fallback)
{{
    const cJSON *it = cJSON_GetObjectItemCaseSensitive(o, key);
    return cJSON_IsNumber(it) ? it->valuedouble : fallback;
}}

static void
_json_add_source(cJSON *so, const {src_struct} *src)
{{
{src_ser_s}
}}

static int
_json_parse_source(const cJSON *so, {src_struct} *src)
{{
    memset(src, 0, sizeof(*src));
{src_parse_s}
    return 0;
}}

static PyObject *
{cname}_to_json({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if (self->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError, "composer already closed");
        return NULL;
    }}
    size_t n;
    int repeat = 0, continuous = 0;
    const {seg_struct} *segs =
        {segments_fn}(self->state, &n, &repeat, &continuous);
    cJSON *root = cJSON_CreateObject();
    if (!root) return PyErr_NoMemory();
    cJSON_AddStringToObject(root, "version", "{module}-1");
    cJSON_AddBoolToObject(root, "repeat", repeat != 0);
    cJSON_AddBoolToObject(root, "continuous", continuous != 0);
    cJSON *arr = cJSON_AddArrayToObject(root, "segments");
    for (size_t i = 0; i < n; i++) {{
        const {seg_struct} *g = &segs[i];
        cJSON *sj = cJSON_CreateObject();
{seg_ser}
        cJSON *srcarr = cJSON_AddArrayToObject(sj, "sources");
        for (size_t k = 0; k < g->{count_member}; k++) {{
            cJSON *so = cJSON_CreateObject();
            _json_add_source(so, &g->{sources_member}[k]);
            cJSON_AddItemToArray(srcarr, so);
        }}
        cJSON_AddItemToArray(arr, sj);
    }}
    char *txt = cJSON_Print(root);
    cJSON_Delete(root);
    if (!txt) return PyErr_NoMemory();
    PyObject *s = PyUnicode_FromString(txt);
    free(txt);
    return s;
}}

/* Build a composer state from a parsed JSON root (NULL on error). */
static {backing}_state_t *
_{backing}_from_root(cJSON *root)
{{
    const cJSON *arr = cJSON_GetObjectItemCaseSensitive(root, "segments");
    if (!cJSON_IsArray(arr) || cJSON_GetArraySize(arr) == 0)
        return NULL;
    int repeat = cJSON_IsTrue(
        cJSON_GetObjectItemCaseSensitive(root, "repeat"));
    int continuous = cJSON_IsTrue(
        cJSON_GetObjectItemCaseSensitive(root, "continuous"));
    size_t n = (size_t)cJSON_GetArraySize(arr);
    {seg_struct} *segs = ({seg_struct} *)calloc(n, sizeof(*segs));
    if (!segs) return NULL;
    size_t i = 0;
    const cJSON *sj = NULL;
    cJSON_ArrayForEach(sj, arr) {{
        {seg_struct} *sg = &segs[i];
        const cJSON *srcarr = cJSON_GetObjectItemCaseSensitive(sj, "sources");
        if (!cJSON_IsArray(srcarr) || cJSON_GetArraySize(srcarr) == 0)
            goto fail;
        size_t ns = (size_t)cJSON_GetArraySize(srcarr);
        {src_struct} *srcs = ({src_struct} *)calloc(ns, sizeof(*srcs));
        if (!srcs) goto fail;
        sg->{sources_member} = srcs;
        sg->{count_member} = ns;
        size_t k = 0;
        const cJSON *so = NULL;
        cJSON_ArrayForEach(so, srcarr) {{
            if (_json_parse_source(so, &srcs[k]) != 0) goto fail;
            k++;
        }}
{seg_parse}
        i++;
    }}
    {{
        {backing}_state_t *st = {create_fn}(segs, n, repeat, continuous);
        for (size_t j = 0; j < n; j++) {{
            if (segs[j].{sources_member})
                for (size_t k = 0; k < segs[j].{count_member}; k++)
                    free(segs[j].{sources_member}[k].bits);
            free(segs[j].{sources_member});
        }}
        free(segs);
        return st;
    }}
fail:
    for (size_t j = 0; j <= i && j < n; j++) {{
        if (segs[j].{sources_member})
            for (size_t k = 0; k < segs[j].{count_member}; k++)
                free(segs[j].{sources_member}[k].bits);
        free(segs[j].{sources_member});
    }}
    free(segs);
    return NULL;
}}

static PyObject *
_{cname}_wrap_state(PyTypeObject *type, {backing}_state_t *st)
{{
    if (!st) {{
        PyErr_SetString(PyExc_ValueError, "invalid composer spec");
        return NULL;
    }}
    {obj} *self = ({obj} *)type->tp_alloc(type, 0);
    if (!self) {{
        {destroy_fn}(st);
        return NULL;
    }}
    self->state = st;
    self->destroyed = 0;
    return (PyObject *)self;
}}

static PyObject *
{cname}_from_json(PyObject *cls, PyObject *args)
{{
    const char *json;
    if (!PyArg_ParseTuple(args, "s", &json))
        return NULL;
    cJSON *root = cJSON_Parse(json);
    if (!root) {{
        PyErr_SetString(PyExc_ValueError, "could not parse JSON");
        return NULL;
    }}
    {backing}_state_t *st = _{backing}_from_root(root);
    cJSON_Delete(root);
    return _{cname}_wrap_state((PyTypeObject *)cls, st); /* cls: subclass round-trips */
}}

static PyObject *
{cname}_from_file(PyObject *cls, PyObject *args)
{{
    PyObject *pathobj;
    if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &pathobj))
        return NULL;
    FILE *fp = fopen(PyBytes_AS_STRING(pathobj), "rb");
    Py_DECREF(pathobj);
    if (!fp) {{
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }}
    fseek(fp, 0, SEEK_END);
    long len = ftell(fp);
    if (len < 0) {{ fclose(fp); PyErr_SetFromErrno(PyExc_OSError); return NULL; }}
    rewind(fp);
    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) {{ fclose(fp); return PyErr_NoMemory(); }}
    size_t rd = fread(buf, 1, (size_t)len, fp);
    fclose(fp);
    buf[rd] = '\\0';
    cJSON *root = cJSON_Parse(buf);
    free(buf);
    if (!root) {{
        PyErr_SetString(PyExc_ValueError, "could not parse JSON file");
        return NULL;
    }}
    {backing}_state_t *st = _{backing}_from_root(root);
    cJSON_Delete(root);
    return _{cname}_wrap_state((PyTypeObject *)cls, st); /* cls: subclass round-trips */
}}
"""


# ── generic composer CLI (c face, gh-287 — retires a hand-written wfmgen) ─────


def composer_cli(cfg: dict, module: str) -> dict:
    """Return the composer's ``[module.X.cli]`` table (opt-in c-face CLI).

    A composer with ``[module.X.cli] enabled = true`` gets a generated
    standalone C command-line tool: source/segment-field flags (enum flags
    validated against the ``[[enum]]`` SSOT), ``--from-file`` (JSON spec via the
    backing parser), and the ``jm app`` output axes (``--sample_type`` /
    ``--file-type`` / ``--endian``). Generic — reusable by any composer."""
    return dict(cfg.get("module", {}).get(module, {}).get("cli", {}))


def render_cli(cfg: dict, module: str) -> str:
    """Render a generic c-face composer CLI (a `main()`), reusing jm app's
    output-axes machinery (`jm_convert_block`/`jm_write_block`) and the SSOT
    enum tables for choice-flag validation — so no hand-written enum tables."""
    from . import _app

    backing = C.capsule_backing(cfg, module)
    header = C.capsule_header(cfg, module) or f"{backing}/{backing}_core.h"
    src = C.composer_source(cfg, module)
    seg = C.composer_segment(cfg, module)
    src_struct, seg_struct = src["struct"], seg["struct"]
    src_fields = list(src.get("fields", []))
    seg_fields = list(seg.get("fields", []))
    sources_member = seg.get("sources_member", "sources")
    count_member = seg.get("count_member", "n_sources")
    create_fn = f"{backing}_create"
    execute_fn = f"{backing}_execute"
    destroy_fn = f"{backing}_destroy"
    from_file_fn = (
        cfg.get("module", {})
        .get(module, {})
        .get("json", {})
        .get("from_file_fn", f"{backing}_from_file")
    )
    sample_types = " ".join(_app._SAMPLE_TYPES)

    # per-field flag decls (defaults), argv parse cases, struct assembly.
    decls, parse, assign = [], [], []
    for f in src_fields:
        n = f["name"]
        if f.get("complex"):
            continue  # complex streams can't cross a generated CLI flag
        if f.get("enum"):
            decls.append(f'    const char *{n} = "{f.get("default", "")}";')
            parse.append(
                f'        else if (!strcmp(a, "--{n}") && i+1<argc) {n} = argv[++i];'
            )
            assign.append(f"""    {{
        int _v = _enum_index(_enum_{f["enum"]}, {n});
        if (_v < 0) {{ fprintf(stderr, "bad --{n} %s\\n", {n}); return 2; }}
        src.{n} = _v;
    }}""")
        elif f.get("bytes"):
            decls.append(f"    const char *{n} = NULL;")
            parse.append(
                f'        else if (!strcmp(a, "--{n}") && i+1<argc) {n} = argv[++i];'
            )
            assign.append(f"""    if ({n}) {{
        size_t _ln = strlen({n});
        uint8_t *_b = (uint8_t *)malloc(_ln ? _ln : 1);
        size_t _k = 0;
        for (size_t _j = 0; _j < _ln; _j++)
            if ({n}[_j] == '0' || {n}[_j] == '1') _b[_k++] = ({n}[_j] - '0');
        src.bits = _b; src.n_bits = _k;
    }}""")
        else:
            ct = f["type"]
            dflt = f.get("default", "0")
            decls.append(f"    {ct} {n} = ({ct}){dflt};")
            conv = (
                f"({ct})strtod(argv[++i], NULL)"
                if ct in ("double", "float")
                else f"({ct})strtoull(argv[++i], NULL, 0)"
            )
            parse.append(
                f'        else if (!strcmp(a, "--{n}") && i+1<argc) {n} = {conv};'
            )
            assign.append(f"        src.{n} = {n};")

    seg_decls, seg_parse, seg_assign = [], [], []
    for f in seg_fields:
        n, ct = f["name"], f["type"]
        dflt = f.get("default", "0")
        seg_decls.append(f"    {ct} {n} = ({ct}){dflt};")
        conv = (
            f"({ct})strtod(argv[++i], NULL)"
            if ct in ("double", "float")
            else f"({ct})strtoull(argv[++i], NULL, 0)"
        )
        seg_parse.append(
            f'        else if (!strcmp(a, "--{n}") && i+1<argc) {n} = {conv};'
        )
        seg_assign.append(f"        seg.{n} = {n};")

    decls_s = "\n".join(decls + seg_decls)
    parse_s = "\n".join(parse + seg_parse)
    assign_s = "\n".join(assign)
    seg_assign_s = "\n".join(seg_assign)
    # The flag path mallocs each bytes field's buffer; <backing>_create
    # deep-copies it, so free our copy after create — keeps the tool
    # Valgrind-clean (constant even under --continuous).
    bytes_free = "".join(
        f"        free(src.{f['name']});\n"
        for f in src_fields
        if f.get("bytes")
    )

    return f"""/*
 * {module}_cli.c — generic composer command-line tool (generated by jm; gh-287).
 *
 * Build a composer from source/segment-field flags or a JSON spec
 * (--from-file), then stream samples in the chosen wire format. Enum flags are
 * validated against the [[enum]] SSOT; no hand-written enum tables.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <complex.h>

#include "{header}"

{_app._SAMPLE_TYPE_C}
{_app._WRITE_BLOCK_C}
{render_enum_tables(cfg, module)}
static int
_st_index(const char *s)
{{
    static const char *const t[] = {{ {", ".join('"' + x + '"' for x in _app._SAMPLE_TYPES)}, NULL }};
    for (int i = 0; t[i]; i++) if (!strcmp(s, t[i])) return i;
    return -1;
}}

static void
usage(const char *prog)
{{
    fprintf(stderr,
        "usage: %s [--<field> V ...] [--from-file SPEC.json]\\n"
        "          [--sample_type {sample_types}] [--file-type raw|csv]\\n"
        "          [--endian le|be] [--out FILE] [--repeat] [--continuous]\\n",
        prog);
}}

int
main(int argc, char **argv)
{{
    const char *from_file = NULL, *out_path = NULL;
    int st = 0, ft = 0, en = 0, repeat = 0, continuous = 0;
{decls_s}
    for (int i = 1; i < argc; i++) {{
        const char *a = argv[i];
        if (!strcmp(a, "--from-file") && i+1<argc) from_file = argv[++i];
        else if (!strcmp(a, "--out") && i+1<argc) out_path = argv[++i];
        else if (!strcmp(a, "--repeat")) repeat = 1;
        else if (!strcmp(a, "--continuous")) continuous = 1;
        else if (!strcmp(a, "--sample_type") && i+1<argc) {{
            st = _st_index(argv[++i]);
            if (st < 0) {{ usage(argv[0]); return 2; }}
        }}
        else if (!strcmp(a, "--file-type") && i+1<argc)
            ft = !strcmp(argv[++i], "csv") ? 1 : 0;
        else if (!strcmp(a, "--endian") && i+1<argc)
            en = !strcmp(argv[++i], "be") ? 1 : 0;
        else if (!strcmp(a, "-h") || !strcmp(a, "--help")) {{ usage(argv[0]); return 0; }}
{parse_s}
        else {{ fprintf(stderr, "unknown arg %s\\n", a); usage(argv[0]); return 2; }}
    }}

    {backing}_state_t *c;
    if (from_file) {{
        c = {from_file_fn}(from_file);
    }} else {{
        {src_struct} src;
        memset(&src, 0, sizeof src);
{assign_s}
        {seg_struct} seg;
        memset(&seg, 0, sizeof seg);
        seg.{sources_member} = &src;
        seg.{count_member} = 1;
{seg_assign_s}
        c = {create_fn}(&seg, 1, repeat, continuous);
{bytes_free}    }}
    if (!c) {{ fprintf(stderr, "failed to build composer\\n"); return 1; }}

    FILE *out = out_path ? fopen(out_path, "wb") : stdout;
    if (!out) {{ perror("fopen"); {destroy_fn}(c); return 1; }}

    float _Complex buf[4096];
    unsigned char bytes[4096 * 16];
    size_t n;
    while ((n = {execute_fn}(c, buf, 4096)) > 0) {{
        jm_write_block(out, buf, n, st, en, ft, bytes);
        if (n < 4096) break;
    }}
    if (out != stdout) fclose(out);
    {destroy_fn}(c);
    return 0;
}}
"""
