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

from . import _config as C

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
    if field.get("enum"):
        return "s"
    if field.get("bytes"):
        return "O"
    return _FMT[field["type"]]


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
    return seen


def render_enum_tables(cfg: dict, module: str) -> str:
    """Emit one ``static const char *const _enum_<name>[]`` table per enum the
    module references, plus a shared ``_enum_index`` lookup. Order **is** the C
    int (the ``[[enum]]`` SSOT contract — append-only), so every face agrees."""
    enums = C.enums(cfg)
    parts = [
        "/* String-enum tables — order is the C int (the [[enum]] SSOT). */",
        "static int",
        "_enum_index(const char *const *tab, const char *s)",
        "{",
        "    for (int i = 0; tab[i]; i++)",
        "        if (strcmp(tab[i], s) == 0)",
        "            return i;",
        "    return -1;",
        "}",
        "",
    ]
    for name in _enums_used(cfg, module):
        values = enums.get(name, [])
        items = "".join(f'    "{v}",\n' for v in values)
        parts.append(f"static const char *const _enum_{name}[] = {{")
        parts.append(items + "    NULL,")
        parts.append("};")
        parts.append("")
    return "\n".join(parts)


# ── source type (e.g. Synth) ─────────────────────────────────────────────────


def _source_fields(cfg: dict, module: str) -> list[dict]:
    return list(C.composer_source(cfg, module).get("fields", []))


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

    parts: list[str] = []

    # struct: backing config + an extra fs (segment owns it in composition, but
    # the source carries it for standalone use), + a borrowed bytes ref kept
    # alive alongside the owned src.bits copy.
    parts.append(f"""typedef struct {{
    PyObject_HEAD
    {struct} src;
    double   fs;
}} {obj};
""")

    # dealloc — free the owned bits buffer.
    parts.append(f"""static void
{tname}_dealloc({obj} *self)
{{
    free(self->src.bits);
    Py_TYPE(self)->tp_free((PyObject *)self);
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
        elif f.get("bytes"):
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
            assign.append(f"""    {{
        int _i = _enum_index(_enum_{e}, {n});
        if (_i < 0) {{
            PyErr_Format(PyExc_ValueError,
                         "invalid {n} '%s'", {n});
            return -1;
        }}
        self->src.{n} = _i;
    }}""")
        elif f.get("bytes"):
            assign.append(f"""    if (!_attach_bytes(&self->src, {n}))
        return -1;""")
        else:
            assign.append(f"    self->src.{n} = {n};")
    assign_s = "\n".join(assign)

    parts.append(f"""/* Copy a Python bytes (0/1 pattern) or None into src->bits (owned). */
static int
_attach_bytes({struct} *src, PyObject *obj)
{{
    free(src->bits);
    src->bits   = NULL;
    src->n_bits = 0;
    if (!obj || obj == Py_None)
        return 1;
    if (!PyBytes_Check(obj)) {{
        PyErr_SetString(PyExc_TypeError, "bits must be bytes or None");
        return 0;
    }}
    Py_ssize_t nb = PyBytes_GET_SIZE(obj);
    if (nb <= 0)
        return 1;
    uint8_t *copy = (uint8_t *)malloc((size_t)nb);
    if (!copy) {{
        PyErr_NoMemory();
        return 0;
    }}
    memcpy(copy, PyBytes_AS_STRING(obj), (size_t)nb);
    src->bits   = copy;
    src->n_bits = (size_t)nb;
    return 1;
}}

static int
{tname}_init({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{{kwlist}, "fs", NULL}};
{decls_s}
    double fs = 1e6;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}", kwlist,
            {addrs_s}, &fs))
        return -1;
    self->fs = fs;
{assign_s}
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
    int _i = _enum_index(_enum_{e}, s);
    if (_i < 0) {{
        PyErr_Format(PyExc_ValueError, "invalid {n} '%s'", s);
        return -1;
    }}
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
    if (self->src.bits && self->src.n_bits)
        return PyBytes_FromStringAndSize(
            (const char *)self->src.bits, (Py_ssize_t)self->src.n_bits);
    Py_RETURN_NONE;
}}
static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    return _attach_bytes(&self->src, value) ? 0 : -1;
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

    parts.append("\n".join(getset_fns))
    parts.append(f"""
static PyGetSetDef {tname}_getset[] = {{
{chr(10).join(getset_rows)}
    {{NULL, NULL, NULL, NULL, NULL}}
}};
""")

    parts.append(f"""static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_new       = PyType_GenericNew,
    .tp_init      = (initproc){tname}_init,
    .tp_dealloc   = (destructor){tname}_dealloc,
    .tp_getset    = {tname}_getset,
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
    return list(C.composer_segment(cfg, module).get("fields", []))


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

    members = "".join(f"    {f['type']} {f['name']};\n" for f in fields)
    parts: list[str] = []
    # Forward declaration — the `sum` classmethod (emitted before the type
    # definition) allocates via the type object.
    parts.append(f"static PyTypeObject {type_obj};\n")
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

    # defaults applied before extraction.
    def _default(f):
        if f.get("default") not in (None, ""):
            return f["default"]
        return "0"

    set_defaults = "\n".join(
        f"    self->{f['name']} = {_default(f)};" for f in fields
    )

    # extract a segment field from a kwargs dict; *delete* (init) or leave
    # (sum). Shared body emitted as a macro-free inline block per field.
    def _extract_block(f, *, delete: bool):
        n, ct = f["name"], f["type"]
        conv = _from_py_scalar(ct, "_o")
        dele = (
            f'        if (PyDict_DelItemString(kw, "{n}") < 0) goto fail;\n'
            if delete
            else ""
        )
        return (
            f'    {{\n        PyObject *_o = PyDict_GetItemString(kw, "{n}");\n'
            f"        if (_o) {{\n"
            f"            self->{n} = {conv};\n"
            f"            if (PyErr_Occurred()) goto fail;\n"
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
    parts.append("\n".join(getset_fns))
    parts.append(f"""
static PyGetSetDef {tname}_getset[] = {{
{chr(10).join(getset_rows)}
    {{NULL, NULL, NULL, NULL, NULL}}
}};

static PyMethodDef {tname}_methods[] = {{
    {{"sum", (PyCFunction)(void (*)(void)){tname}_sum,
     METH_VARARGS | METH_KEYWORDS | METH_CLASS,
     "sum(*sources, **segment_fields) -> {tname}"}},
    {{NULL, NULL, 0, NULL}}
}};
""")

    parts.append(f"""static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT,
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
