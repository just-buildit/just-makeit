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

    # dealloc — destroy any built generator, then free the owned bits buffer.
    gen_dtor = (
        f"    if (self->_gen) {gen['destroy_fn']}(self->_gen);\n"
        if gen
        else ""
    )
    parts.append(f"""static void
{tname}_dealloc({obj} *self)
{{
{gen_dtor}    free(self->src.bits);
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
{"    self->_gen = NULL;" + chr(10) if gen else ""}{assign_s}
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
    # The Timeline type `add` sequences into; declared (and defined) after
    # Segment, so it is forward-declared here.
    tl_tname = C.composer_timeline(cfg, module).get("type_name")

    members = "".join(f"    {f['type']} {f['name']};\n" for f in fields)
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
    pkg_path = C.capsule_package(cfg, module) or C.module_paths(module).pypath
    dotted = f"{pkg}.{pkg_path.replace('/', '.')}.{cname}"
    obj = f"{cname}Object"
    type_obj = f"{cname}Type"

    create_fn = f"{backing}_create"
    execute_fn = f"{backing}_execute"
    segments_fn = f"{backing}_segments"
    destroy_fn = f"{backing}_destroy"

    # segment scalar field copy lines (both directions).
    fwd = "\n".join(
        f"        segs[i].{f['name']} = seg->{f['name']};" for f in seg_fields
    )
    back = "\n".join(
        f"        sg->{f['name']} = src[i].{f['name']};" for f in seg_fields
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
    (void)cls;
    const char *json;
    if (!PyArg_ParseTuple(args, "s", &json))
        return NULL;
    {backing}_state_t *st = {from_json_fn}(json);
    if (!st) {{
        PyErr_SetString(PyExc_ValueError, "{from_json_fn} failed");
        return NULL;
    }}
    {obj} *self = ({obj} *){type_obj}.tp_alloc(&{type_obj}, 0);
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
    (void)cls;
    PyObject *pathobj;
    if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &pathobj))
        return NULL;
    {backing}_state_t *st = {from_file_fn}(PyBytes_AS_STRING(pathobj));
    Py_DECREF(pathobj);
    if (!st) {{
        PyErr_SetString(PyExc_OSError, "{from_file_fn} failed");
        return NULL;
    }}
    {obj} *self = ({obj} *){type_obj}.tp_alloc(&{type_obj}, 0);
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
            syn->src = src[i].{sources_member}[k]; /* scalars + bits ptr */
            syn->fs = src[i].fs;
            if (syn->src.bits && syn->src.n_bits) {{
                uint8_t *copy = (uint8_t *)malloc(syn->src.n_bits);
                if (copy)
                    memcpy(copy, syn->src.bits, syn->src.n_bits);
                syn->src.bits = copy;
            }} else {{
                syn->src.bits = NULL;
                syn->src.n_bits = 0;
            }}
            PyList_SET_ITEM(srclist, (Py_ssize_t)k, (PyObject *)syn);
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

static PyMethodDef {cname}_methods[] = {{
    {{"execute", (PyCFunction){cname}_execute, METH_VARARGS,
     "execute(n) -> ndarray[complex64]"}},
    {{"compose", (PyCFunction)(void (*)(void)){cname}_compose,
     METH_VARARGS | METH_KEYWORDS, "compose(block=4096) -> ndarray[complex64]"}},
    {{"close", (PyCFunction){cname}_close, METH_NOARGS, "close() -> None"}},
    {{"__enter__", (PyCFunction){cname}_enter, METH_NOARGS, NULL}},
    {{"__exit__", (PyCFunction){cname}_exit, METH_VARARGS, NULL}},
{json_rows}    {{NULL, NULL, 0, NULL}}
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
    gen = _source_generates(cfg, module)
    gen_includes = ""
    if gen:
        src_struct = C.composer_source(cfg, module)["struct"]
        gen_includes = (
            f'#include "{gen["header"]}"\n'
            "/* project bridge (straight C, no CPython): build the generator. */\n"
            f"extern {gen['state_type']} *{gen['bridge_fn']}("
            f"const {src_struct} *, double);\n"
        )

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
{gen_includes}{json_includes}""",
        render_enum_tables(cfg, module),
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


def _pyi_field_sig(fields: list[dict]) -> str:
    """Keyword signature fragment for a source/segment field list."""
    out = []
    for f in fields:
        if f.get("enum"):
            out.append(f"{f['name']}: str = ...")
        elif f.get("bytes"):
            out.append(f"{f['name']}: bytes | None = ...")
        elif f["type"] in ("double", "float"):
            out.append(f"{f['name']}: float = ...")
        else:
            out.append(f"{f['name']}: int = ...")
    return ", ".join(out)


def render_pyi(cfg: dict, module: str) -> str:
    """Render a typed ``.pyi`` stub for the composer's OO surface.

    Signatures only (rich docstrings are a header-derived follow-up, as with the
    capsule). Covers the source / segment / timeline / composer types, the
    factories, and — when enabled — the JSON faces."""
    src = C.composer_source(cfg, module)
    seg = C.composer_segment(cfg, module)
    oo = C.composer_oo(cfg, module)
    src_t = src["type_name"]
    seg_t = seg["type_name"]
    tl_t = C.composer_timeline(cfg, module).get("type_name")
    cname = oo.get("composer_type_name", "Composer")
    src_sig = _pyi_field_sig(src.get("fields", []))
    seg_scalar_sig = _pyi_field_sig(seg.get("fields", []))

    lines = [
        f"# {C.module_paths(module).leaf}.pyi — composer OO types (jm; gh-287).",
        "from __future__ import annotations",
        "from typing import Any",
        "import numpy as np",
        "from numpy.typing import NDArray",
        "",
        f"class {src_t}:",
        f"    def __init__(self, {src_sig}{', ' if src_sig else ''}"
        "fs: float = ...) -> None: ...",
        "    def __getattr__(self, name: str) -> Any: ...",
    ]
    if _source_generates(cfg, module):
        lines += [
            "    def steps(self, n: int) -> NDArray[np.complex64]: ...",
            "    def step(self) -> complex: ...",
            "    def reset(self) -> None: ...",
        ]
    lines += [
        "",
        f"class {seg_t}:",
        f"    sources: list[{src_t}]",
        f"    def __init__(self, {src_sig}{', ' if src_sig else ''}"
        f"{seg_scalar_sig}) -> None: ...",
        "    @classmethod",
        f"    def sum(cls, *sources: {src_t}, {seg_scalar_sig}) -> {seg_t}: ...",
    ]
    if tl_t:
        lines.append(f"    def add(self, *others: {seg_t}) -> {tl_t}: ...")
        lines += [
            "",
            f"class {tl_t}:",
            f"    segments: list[{seg_t}]",
            f"    def __init__(self, segments: list[{seg_t}]) -> None: ...",
            f"    def add(self, *segments: {seg_t}) -> {tl_t}: ...",
            "    def __iter__(self): ...",
            "    def __len__(self) -> int: ...",
            "    def __getitem__(self, i): ...",
        ]
    seg_or_tl = (
        f"{seg_t} | {tl_t} | list[{seg_t}] | None"
        if tl_t
        else (f"{seg_t} | list[{seg_t}] | None")
    )
    lines += [
        "",
        f"class {cname}:",
        f"    segments: list[{seg_t}]",
        "    repeat: bool",
        "    continuous: bool",
        f"    def __init__(self, segments: {seg_or_tl} = ..., *, "
        "repeat: bool = ..., continuous: bool = ..., **segment_kwargs"
        ") -> None: ...",
        "    def execute(self, n: int) -> NDArray[np.complex64]: ...",
        "    def compose(self, block: int = ...) -> NDArray[np.complex64]: ...",
        "    def close(self) -> None: ...",
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
    # factories
    for fac in oo.get("factories", []):
        lines.append(f"def {fac}(**kw: Any) -> {src_t}: ...")
    lines.append("")
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
    seg_fields = list(seg.get("fields", []))
    src_fields = list(src.get("fields", []))
    sources_member = seg.get("sources_member", "sources")
    count_member = seg.get("count_member", "n_sources")
    cname = oo.get("composer_type_name", "Composer")
    obj = f"{cname}Object"
    type_obj = f"{cname}Type"
    create_fn = f"{backing}_create"
    segments_fn = f"{backing}_segments"
    destroy_fn = f"{backing}_destroy"

    # ── to_json: serialize one source object ──
    src_ser = []
    for f in src_fields:
        n = f["name"]
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
        else:
            src_ser.append(
                f'        cJSON_AddNumberToObject(so, "{n}", '
                f"(double)src->{n});"
            )
    src_ser_s = "\n".join(src_ser)

    seg_ser = "\n".join(
        f'        cJSON_AddNumberToObject(sj, "{f["name"]}", '
        f"(double)g->{f['name']});"
        for f in seg_fields
    )

    # ── from_json: parse one source object into *src ──
    src_parse = []
    for f in src_fields:
        n = f["name"]
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
        else:
            ct = f["type"]
            src_parse.append(
                f'        src->{n} = ({ct})_json_num(so, "{n}", '
                f"{_default_literal(f)});"
            )
    src_parse_s = "\n".join(src_parse)

    seg_parse = "\n".join(
        f'        sg->{f["name"]} = ({f["type"]})_json_num(sj, "{f["name"]}", '
        f"{_default_literal(f)});"
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
_{cname}_wrap_state({backing}_state_t *st)
{{
    if (!st) {{
        PyErr_SetString(PyExc_ValueError, "invalid composer spec");
        return NULL;
    }}
    {obj} *self = ({obj} *){type_obj}.tp_alloc(&{type_obj}, 0);
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
    (void)cls;
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
    return _{cname}_wrap_state(st);
}}

static PyObject *
{cname}_from_file(PyObject *cls, PyObject *args)
{{
    (void)cls;
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
    return _{cname}_wrap_state(st);
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
