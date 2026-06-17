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
            f'    {{"{fac}", (PyCFunction)_factory_{fac}, '
            f"METH_VARARGS | METH_KEYWORDS,\n"
            f'     "{fac}(**kw) -> {C.composer_source(cfg, module)["type_name"]}"}},'
        )
    return rows
