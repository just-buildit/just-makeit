"""
_handle.py — code generator for ``kind = "handle"`` modules (gh-306).

A handle module is the **intersection** of jm's two existing generators:

- the **capsule** generator (:mod:`_capsule`, gh-286) gives an opaque hand-C
  backing + lifecycle + numpy marshaling, but emits **free functions**;
- the **composer** generator (:mod:`_composer`, gh-287) emits a typed
  ``PyTypeObject`` (struct + ``tp_init`` + getsets + context-manager +
  idempotent close), but its state must be a jm-introspected struct.

``kind = "handle"`` = *capsule's opaque backing* + *composer's typed-class face*:
it emits one CPython class over an OPAQUE hand-C resource handle::

    w = Writer(path, file_type="raw", sample_type="cf32")   # __init__ -> *_open
    n = w.write(iq)                                          # numpy in -> size
    w.clip_fraction                                          # decoded-getter prop
    with Writer(path) as w: ...                              # RAII -> *_close

Almost everything is **reused** from the two generators: enum-string ↔ index
tables + ``_enum_index`` (composer SSOT), scalar format-char machinery
(:data:`_types._CTYPE_META`), numpy in/out marshaling (capsule), and the
context-manager + idempotent-close + ``tp_dealloc`` pattern (composer). The only
**genuinely new** C is the *decoded-getter property* (getter → out-struct →
named fields with enum / scale / expr transforms) and the *weak-symbol backend
guard*; everything else calls the reused helpers.
"""

from __future__ import annotations

from pathlib import Path

from . import _capsule
from . import _composer
from . import _config as C
from . import _types as T

# ── small type helpers (reused from _capsule / _types) ───────────────────────


def _scalar_fmt(ctype: str) -> str:
    """PyArg_ParseTuple format char for a scalar C type (reuses _CTYPE_META)."""
    return _capsule._scalar_fmt(ctype)


def _to_py(ctype: str, expr: str) -> str:
    """C expression converting a scalar C value to a new PyObject (reused)."""
    return _capsule._to_py(ctype, expr)


def _array_elem_npy(array_type: str) -> tuple[str, str]:
    """(C element type, NPY enum) for a ``T[]`` array type (reuses capsule)."""
    return _capsule._array_elem_npy(array_type)


# ── enum SSOT → C tables (reuses the composer's _enum_index contract) ─────────


def _enums_used(cfg: dict, module: str) -> list[str]:
    """Ordered, de-duplicated enum names referenced by a handle module — by its
    ``create_args`` and any ``getters[].fields[].enum``. Mirrors the composer's
    ``_enums_used`` (which scans the source/segment tables) for the handle
    field set, so we emit only the tables the type actually needs."""
    seen: list[str] = []
    for a in C.handle_create_args(cfg, module):
        e = a.get("enum")
        if e and e not in seen:
            seen.append(e)
    for g in C.handle_getters(cfg, module):
        for f in g.get("fields", []):
            e = f.get("enum")
            if e and e not in seen:
                seen.append(e)
    return seen


def render_enum_tables(cfg: dict, module: str) -> str:
    """Emit the per-enum ``_enum_<name>[]`` tables + the shared ``_enum_index``.

    Reuses the composer's enum SSOT exactly — the same ``_enum_index`` lookup
    body and the same "order is the C int" table layout — over the handle's own
    field set (:func:`_enums_used`)."""
    enums = C.enums(cfg)
    parts = [_composer._ENUM_INDEX_FN]
    for name in _enums_used(cfg, module):
        values = enums.get(name, [])
        items = "".join(f'    "{v}",\n' for v in values)
        parts.append(f"static const char *const _enum_{name}[] = {{")
        parts.append(items + "    NULL,")
        parts.append("};")
        parts.append("")
    return "\n".join(parts)


# ── stashed init scalars (referenced by decoded-getter exprs) ─────────────────


def _stash_inits(cfg: dict, module: str) -> list[tuple[str, str]]:
    """The init scalars an ``expr`` field references via ``self-><init>``.

    A decoded-getter ``expr`` may read a constructor value (``self->sample_type
    >= 2``); those must be stashed into the object struct in ``tp_init``. We
    stash every scalar/enum create-arg whose name appears in some ``expr`` — as
    ``(name, c_type)`` (enum args stash as ``int``)."""
    exprs = " ".join(
        f.get("expr", "")
        for g in C.handle_getters(cfg, module)
        for f in g.get("fields", [])
    )
    out: list[tuple[str, str]] = []
    for a in C.handle_create_args(cfg, module):
        n = a["name"]
        if a.get("type") == "path":
            continue
        if f"self->{n}" in exprs:
            ctype = "int" if a.get("enum") else a["type"]
            out.append((n, ctype))
    return out


# ── tp_init (opaque create → handle; mirrors capsule create + composer init) ──


def _arg_decl(a: dict) -> str:
    """C local decl for one create-arg, with its manifest default."""
    n = a["name"]
    if a.get("type") == "path":
        return f"    PyObject *{n} = NULL;  /* fspath -> bytes */"
    if a.get("enum"):
        default = a.get("default", "")
        return f'    const char *{n} = "{default}";'
    default = a.get("default", "0")
    return f"    {a['type']} {n} = {default};"


def _arg_fmt(a: dict) -> str:
    """PyArg_ParseTupleAndKeywords format char for one create-arg."""
    if a.get("type") == "path":
        return "O&"  # PyUnicode_FSConverter
    if a.get("enum"):
        return "s"
    return _scalar_fmt(a["type"])


def _arg_addr(a: dict) -> str:
    """The ``&target`` (or converter+target) PyArg address fragment."""
    n = a["name"]
    if a.get("type") == "path":
        return f"PyUnicode_FSConverter, &{n}"
    return f"&{n}"


def _create_call_arg(a: dict) -> str:
    """The expression passed to ``create_fn`` for one create-arg."""
    n = a["name"]
    if a.get("type") == "path":
        # PyUnicode_FSConverter yields a PyBytes; the C side copies the path.
        return f"PyBytes_AS_STRING({n})"
    if a.get("enum"):
        return f"_arg_{n}"  # validated enum index local
    return n


def render_tp_init(cfg: dict, module: str) -> str:
    """Emit the handle's ``tp_init``: coerce ``create_args``, call ``create_fn``,
    run ``create_post`` setters, stash expr-referenced inits.

    Reuses the composer's keyword-``tp_init`` shape (all-optional ``|`` format,
    enum-string → index via ``_enum_index``) and the capsule's create skeleton
    (call backing ctor, NULL → error). New only at the path coercion (``O&`` +
    ``PyUnicode_FSConverter``) and the weak-symbol guard.

    **gh-219 UAF trap:** a ``path`` arg crosses as a borrowed ``PyBytes`` (from
    ``PyUnicode_FSConverter``); ``create_fn`` must COPY it (the hand-C
    ``*_open`` does — it ``fopen``s / dups the string), so we hold the bytes
    referenced across the call and ``Py_DECREF`` only AFTER ``create_fn``
    returns. We never hand the C side a buffer that outlives this borrow."""
    C.handle_backing(cfg, module)
    tname = C.handle_type_name(cfg, module)
    obj = f"{tname}Object"
    create_fn = C.handle_create_fn(cfg, module)
    init_fn = C.handle_init_fn(cfg, module)
    htype = C.handle_type(cfg, module)
    args = C.handle_create_args(cfg, module)
    opt_backend = C.handle_optional_backend(cfg, module)

    kwlist = ", ".join(f'"{a["name"]}"' for a in args)
    fmt = "|" + "".join(_arg_fmt(a) for a in args)
    decls = "\n".join(_arg_decl(a) for a in args)
    addrs = ", ".join(_arg_addr(a) for a in args)

    # Weak-symbol backend guard (NEW): raise NotImplementedError when the
    # optional backing isn't linked on this platform.
    guard = ""
    if opt_backend:
        guard = f"""    if (&{opt_backend} == NULL || !{opt_backend}) {{
        PyErr_SetString(PyExc_NotImplementedError,
            "{tname}: backend `{opt_backend}` is not available "
            "on this platform");
        return -1;
    }}
"""

    # Enum args: validate the parsed string to its SSOT int (reused contract).
    enum_validate = []
    for a in args:
        if a.get("enum"):
            n, e = a["name"], a["enum"]
            enum_validate.append(f"""    int _arg_{n} = _enum_index(_enum_{e}, {n});
    if (_arg_{n} < 0) {{
        PyErr_Format(PyExc_ValueError, "invalid {n} '%s'", {n});{_init_fsfree(args)}
        return -1;
    }}""")
    enum_validate_s = "\n".join(enum_validate)

    call_args = ", ".join(_create_call_arg(a) for a in args)
    # path borrows must be DECREF'd only after create_fn copies them (gh-219).
    fs_args = [a for a in args if a.get("type") == "path"]
    fs_decref = "".join(f"    Py_XDECREF({a['name']});\n" for a in fs_args)

    # create_post setters (optional `when` guard + verbatim-C `arg`).
    post = []
    for p in C.handle_create_post(cfg, module):
        body = (
            f"{p['fn']}(self->h, {p['arg']});"
            if "arg" in p
            else f"{p['fn']}(self->h);"
        )
        if p.get("when"):
            post.append(f"    if ({p['when']})\n        {body}")
        else:
            post.append(f"    {body}")
    post_s = "\n".join(post)

    # Stash expr-referenced init scalars into the object struct.
    stash = []
    for n, _ct in _stash_inits(cfg, module):
        src = (
            f"_arg_{n}"
            if any(a["name"] == n and a.get("enum") for a in args)
            else n
        )
        stash.append(f"    self->{n} = {src};")
    stash_s = "\n".join(stash)

    # Resolve every cache=true getter once into its stashed out-struct (read
    # after create_fn + create_post so it reflects the fully-built handle). The
    # cache=true getset then reads self->_g<i> instead of calling the getter.
    cache_s = _cache_fetch(cfg, module)

    parse_fail = f"{fs_decref}        return -1;"

    # Construct the handle: either create_fn (allocates + returns it) or, for an
    # init-in-place API (gh-315), jm mallocs sizeof(handle) and calls init_fn on
    # it. init_fn returns void, so the only failure to check is the malloc.
    if init_fn:
        construct = f"""    self->h = ({htype} *)malloc(sizeof({htype}));
{fs_decref}    if (!self->h) {{
        PyErr_NoMemory();
        return -1;
    }}
    {init_fn}(self->h{(", " + call_args) if call_args else ""});"""
    else:
        construct = f"""    self->h = {create_fn}({call_args});
{fs_decref}    if (!self->h) {{
        PyErr_SetString(PyExc_RuntimeError, "{create_fn} failed");
        return -1;
    }}"""

    return f"""static int
{tname}_init({obj} *self, PyObject *args, PyObject *kwds)
{{
{guard}    static char *kwlist[] = {{{kwlist}, NULL}};
{decls}
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}", kwlist,
            {addrs})) {{
{parse_fail}
    }}
{enum_validate_s}
{construct}
    self->closed = 0;
{stash_s}
{post_s}
{cache_s}
    return 0;
}}
"""


def _init_fsfree(args: list[dict]) -> str:
    """Free path borrows on an enum-validation error inside tp_init."""
    fs = [a for a in args if a.get("type") == "path"]
    if not fs:
        return ""
    return "\n" + "".join(f"        Py_XDECREF({a['name']});" for a in fs)


# ── tp_methods (scalar / array-in / int-in→array-out; reuses capsule numpy) ───


def _method_kwargs(m: dict) -> bool:
    """True if a method takes keyword/default args (#319): a scalar-arg method
    (shape (a)) with at least one arg. Such methods parse with
    ``PyArg_ParseTupleAndKeywords`` and register as ``METH_VARARGS |
    METH_KEYWORDS`` so ``track_clipping(on=True)`` and a ``default`` both work;
    the array shapes (b)-(d) stay positional ``METH_VARARGS``."""
    margs = m.get("args", [])
    if not margs:
        return False
    return not any(str(a.get("type", "")).endswith("[]") for a in margs)


def _emit_method(cfg: dict, module: str, m: dict) -> str:
    """Emit one handle method calling ``fn(self->h, …)``.

    Four shapes (the doppler archetype):
      (a) scalar(s) → scalar return — ``track_clipping(on) -> None``;
      (b) array-in (+ optional trailing scalars) → scalar return — ``write(iq)
          -> size_t`` and ``send(iq, fs, fc) -> None`` (reuses the capsule
          ``PyArray_FROM_OTF`` input marshaling; the scalars parse after the
          array and pass straight through to ``fn(h, in_data, n_in, …)``, #308);
      (c) int-in → array-out — ``read(n) -> ndarray`` (allocates an out array of
          size n, calls ``fn(h, out, n) -> actual``, returns an INDEPENDENT
          numpy-owned array trimmed to ``actual`` — never a dangling view; the
          gh-219 grow-on-demand fix shape);
      (d) array-in + writable array-out → array view — ``execute(x, out)``
          (marshals a borrowed input and a writable exact-dtype output, calls
          ``fn(h, in, n_in, out, max_out) -> n_out``, returns the zero-copy
          ``out[:n_out]``; mirrors the capsule ``_emit_execute``, #311)."""
    tname = C.handle_type_name(cfg, module)
    obj = f"{tname}Object"
    name, fn = m["name"], m["fn"]
    margs = list(m.get("args", []))
    returns = m.get("returns")
    nogil = bool(m.get("nogil"))
    gil_open = "    Py_BEGIN_ALLOW_THREADS\n" if nogil else ""
    gil_close = "    Py_END_ALLOW_THREADS\n" if nogil else ""

    closed_guard = f"""    if (self->closed) {{
        PyErr_SetString(PyExc_RuntimeError, "{tname} is closed");
        return NULL;
    }}"""

    array_in = [a for a in margs if str(a.get("type", "")).endswith("[]")]

    # (c) int-in → array-out: an array return with a single integer arg.
    if returns and str(returns).endswith("[]") and not array_in:
        out_elem, out_npy = _array_elem_npy(returns)
        cnt = margs[0]["name"] if margs else "n"
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
    Py_ssize_t {cnt};
    if (!PyArg_ParseTuple(args, "n", &{cnt})) return NULL;
{closed_guard}
    if ({cnt} < 0) {{
        PyErr_SetString(PyExc_ValueError, "count must be >= 0");
        return NULL;
    }}
    npy_intp dims[] = {{{cnt}}};
    /* Independent numpy-owned array — never a view into a grow-on-demand
     * buffer (gh-219): the returned array owns its data outright. */
    PyObject *arr = PyArray_SimpleNew(1, dims, {out_npy});
    if (!arr) return NULL;
    {out_elem} *out = ({out_elem} *)PyArray_DATA((PyArrayObject *)arr);
    size_t got;
{gil_open}    got = {fn}(self->h, out, (size_t){cnt});
{gil_close}    PyArray_DIMS((PyArrayObject *)arr)[0] = (npy_intp)got; /* trim */
    return arr;
}}
"""

    # (d) array-in + writable array-out → out[:n_out] view (#311). A writable
    # array arg + an array return marks a caller-buffer execute: marshal a
    # borrowed input and a writable exact-dtype output (no silent cast — a cast
    # would write into a temp copy, not the caller's buffer), call
    # fn(h, in, n_in, out, max_out), and return the zero-copy view out[:n_out],
    # which pins the caller's array (gh-219). Mirrors capsule _emit_execute.
    writable_out = [a for a in array_in if a.get("writable")]
    if writable_out and returns and str(returns).endswith("[]"):
        o = writable_out[0]
        ins = [a for a in array_in if not a.get("writable")]
        if len(writable_out) > 1 or len(ins) != 1:
            raise NotImplementedError(
                f"handle method '{name}': shape (d) takes exactly one input "
                "array and one writable output array"
            )
        a = ins[0]
        xn, on = a["name"], o["name"]
        in_elem, in_npy = _array_elem_npy(a["type"])
        out_elem, out_npy = _array_elem_npy(o["type"])
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
    PyObject *{xn}_obj, *{on}_obj;
    if (!PyArg_ParseTuple(args, "OO", &{xn}_obj, &{on}_obj)) return NULL;
{closed_guard}
    PyArrayObject *{xn}_arr = (PyArrayObject *)PyArray_FROM_OTF(
        {xn}_obj, {in_npy}, NPY_ARRAY_C_CONTIGUOUS);
    if (!{xn}_arr) return NULL;

    /* Require the exact output dtype — no silent cast (a cast writes into a
     * temp copy instead of the caller's buffer). */
    if (!PyArray_Check({on}_obj) ||
        PyArray_TYPE((PyArrayObject *){on}_obj) != {out_npy} ||
        !PyArray_ISWRITEABLE((PyArrayObject *){on}_obj)) {{
        PyErr_SetString(PyExc_TypeError,
            "{on} must be a writable ndarray of the output dtype");
        Py_DECREF({xn}_arr);
        return NULL;
    }}
    PyArrayObject *{on}_arr = (PyArrayObject *)PyArray_FROM_OTF(
        {on}_obj, {out_npy}, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE);
    if (!{on}_arr) {{ Py_DECREF({xn}_arr); return NULL; }}

    size_t n_in    = (size_t)PyArray_SIZE({xn}_arr);
    size_t max_out = (size_t)PyArray_SIZE({on}_arr);
    const {in_elem} *in_data = (const {in_elem} *)PyArray_DATA({xn}_arr);
    {out_elem} *out_data = ({out_elem} *)PyArray_DATA({on}_arr);
    size_t n_out;
{gil_open}    n_out = {fn}(self->h, in_data, n_in, out_data, max_out);
{gil_close}    Py_DECREF({xn}_arr);

    /* Return {on}_arr[:n_out] — zero-copy view into the caller's buffer. */
    PyObject *stop  = PyLong_FromSsize_t((Py_ssize_t)n_out);
    PyObject *slice = stop ? PySlice_New(NULL, stop, NULL) : NULL;
    Py_XDECREF(stop);
    PyObject *view  = slice ? PyObject_GetItem((PyObject *){on}_arr, slice)
                            : NULL;
    Py_XDECREF(slice);
    Py_DECREF({on}_arr);
    return view;
}}
"""

    # (b) array-in (+ optional trailing scalars) → scalar / None return.
    # The array marshals like the capsule path; any further scalar args parse
    # after it and pass through to fn(self->h, in_data, n_in, <scalars>) — e.g.
    # ZmqSink.send(iq, fs, fc) -> wfm_zmq_sink_send(h, iq, n, fs, fc) (#308).
    if array_in:
        a = array_in[0]
        in_elem, in_npy = _array_elem_npy(a["type"])
        others = [s for s in margs if s is not a]
        if any(str(s.get("type", "")).endswith("[]") for s in others):
            raise NotImplementedError(
                f"handle method '{name}': more than one array arg is "
                "unsupported"
            )
        scal_decls = "".join(f"    {s['type']} {s['name']};\n" for s in others)
        scal_fmt = "".join(_scalar_fmt(s["type"]) for s in others)
        scal_addrs = "".join(f", &{s['name']}" for s in others)
        scal_call = "".join(f", {s['name']}" for s in others)
        ret_to_py = (
            _to_py(returns, "r")
            if returns
            else "(Py_INCREF(Py_None), Py_None)"
        )
        ret_decl = f"    {returns} r;\n" if returns else ""
        assign = "r = " if returns else ""
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
    PyObject *x_obj;
{scal_decls}    if (!PyArg_ParseTuple(args, "O{scal_fmt}", &x_obj{scal_addrs}))
        return NULL;
{closed_guard}
    PyArrayObject *x_arr = (PyArrayObject *)PyArray_FROM_OTF(
        x_obj, {in_npy}, NPY_ARRAY_C_CONTIGUOUS);
    if (!x_arr) return NULL;
    size_t n_in = (size_t)PyArray_SIZE(x_arr);
    const {in_elem} *in_data = (const {in_elem} *)PyArray_DATA(x_arr);
{ret_decl}{gil_open}    {assign}{fn}(self->h, in_data, n_in{scal_call});
{gil_close}    Py_DECREF(x_arr);
    return {ret_to_py};
}}
"""

    # (a) scalar(s) → scalar / None. With args, support keyword passing +
    # defaults (#319) via PyArg_ParseTupleAndKeywords (mirrors tp_init and
    # module functions); a no-arg method stays a plain METH_VARARGS stub.
    call = ", ".join(["self->h"] + [a["name"] for a in margs])
    if returns:
        ret = f"""    {returns} r;
{gil_open}    r = {fn}({call});
{gil_close}    return {_to_py(returns, "r")};"""
    else:
        ret = f"""{gil_open}    {fn}({call});
{gil_close}    Py_RETURN_NONE;"""

    if margs:
        decls = ""
        fmt_parts = []
        inserted = False
        for a in margs:
            d = a.get("default")
            init = f" = {d}" if d is not None else ""
            decls += f"    {a['type']} {a['name']}{init};\n"
            if d is not None and not inserted:
                fmt_parts.append("|")  # everything after is optional
                inserted = True
            fmt_parts.append(_scalar_fmt(a["type"]))
        fmt = "".join(fmt_parts)
        kwlist = ", ".join(f'"{a["name"]}"' for a in margs)
        addrs = ", ".join(f"&{a['name']}" for a in margs)
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{{kwlist}, NULL}};
{decls}    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}", kwlist,
            {addrs})) return NULL;
{closed_guard}
{ret}
}}
"""

    return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
    (void)args;
{closed_guard}
{ret}
}}
"""


# ── getsets (decoded-getter property — THE genuinely-new C) ───────────────────


def _scalar_out(g: dict) -> bool:
    """True if a getter's ``out`` is a scalar C type (return-by-value) rather
    than a struct filled through an out-pointer. A scalar getter ``T fn(h)``
    (e.g. ``double ddcr_get_norm_freq(s)``) backs a single writable property;
    a struct getter ``void fn(h, T *out)`` backs N decoded fields (#311).
    ``.get`` not ``[]`` so a per-field-getter table (no ``out``) is safe (#314)."""
    return g.get("out", "") in T._CTYPE_META


def _decode_field(f: dict, scalar: bool) -> str:
    """Decode one field's value (held in the C local ``tmp``) into a PyObject.

    The access expression is ``tmp`` for a scalar value (a scalar struct-getter
    ``out``, or a per-field ``getter``) or ``tmp.<from|name>`` for a struct
    getter's named member. The transform menu then applies:
      plain   → ``_to_py(<acc>)`` (reuses _CTYPE_META);
      enum    → ``PyUnicode_FromString(_enum_<e>[<acc>])`` (composer SSOT);
      scale   → ``_to_py(<acc> * <scale>)``;
      expr    → the verbatim-C ``expr`` (may reference ``tmp`` / ``tmp.<f>`` +
                stashed ``self-><init>``)."""
    acc = "tmp" if scalar else f"tmp.{f.get('from', f['name'])}"
    if f.get("expr"):
        return _to_py(f["type"], f["expr"])
    if f.get("enum"):
        return f"PyUnicode_FromString(_enum_{f['enum']}[{acc}])"
    if "scale" in f:
        return _to_py(f["type"], f"{acc} * {f['scale']}")
    return _to_py(f["type"], acc)


def render_getsets(cfg: dict, module: str) -> tuple[str, str]:
    """Emit the decoded-getter getter functions + the getset table.

    Returns ``(funcs, table_name)``. For each ``getters[]`` entry we call the
    shared C getter ``fn(self->h, &tmp)`` once per property access (or read a
    cached ``tmp`` resolved in ``tp_init`` when ``cache = true``), then decode
    each declared field. This getter→struct→named-fields decode is the new code;
    the getset *table* shape mirrors the composer's read-only getsets."""
    tname = C.handle_type_name(cfg, module)
    obj = f"{tname}Object"
    funcs: list[str] = []
    rows: list[str] = []

    closed_get = f"""    if (self->closed) {{
        PyErr_SetString(PyExc_RuntimeError, "{tname} is closed");
        return NULL;
    }}"""

    for gi, g in enumerate(C.handle_getters(cfg, module)):
        # A table with a shared `fn` fills one `tmp` per access (struct), or
        # returns it by value (scalar out), or reads the cached tmp (cache=true).
        # A table whose fields each name their own `getter` has no shared fetch
        # (#314); each field call sites its own scalar getter below.
        table_fetch = ""
        scalar = False
        if g.get("fn"):
            out_t = g["out"]
            scalar = _scalar_out(g)
            if g.get("cache"):
                table_fetch = f"    {out_t} tmp = self->_g{gi};"
            elif scalar:
                table_fetch = f"""    {out_t} tmp;
{closed_get}
    tmp = {g["fn"]}(self->h);"""
            else:
                table_fetch = f"""    {out_t} tmp;
{closed_get}
    {g["fn"]}(self->h, &tmp);"""
        for f in g.get("fields", []):
            n = f["name"]
            field_getter = f.get("getter")
            if field_getter:
                # #314: a field with its own scalar getter `T fn(h)` — fetch
                # per field (live, return-by-value), decode `tmp` as a scalar.
                fetch = f"""    {f["type"]} tmp;
{closed_get}
    tmp = {field_getter}(self->h);"""
                f_scalar = True
            else:
                fetch = table_fetch
                f_scalar = scalar
            funcs.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
{fetch}
    return {_decode_field(f, f_scalar)};
}}
""")
            # A field naming a `writable_fn` also emits a (setter) slot calling
            # set_fn(self->h, v) with v coerced from the PyObject (#311).
            set_fn = f.get("writable_fn")
            if set_fn:
                fmt = _scalar_fmt(f["type"])
                funcs.append(f"""static int
{tname}_set_{n}({obj} *self, PyObject *value, void *closure)
{{
    (void)closure;
    if (self->closed) {{
        PyErr_SetString(PyExc_RuntimeError, "{tname} is closed");
        return -1;
    }}
    if (value == NULL) {{
        PyErr_SetString(PyExc_AttributeError,
            "cannot delete '{n}'");
        return -1;
    }}
    {f["type"]} v;
    if (!PyArg_Parse(value, "{fmt}", &v)) return -1;
    {set_fn}(self->h, v);
    return 0;
}}
""")
                rows.append(
                    f'    {{"{n}", (getter){tname}_get_{n}, '
                    f"(setter){tname}_set_{n}, NULL, NULL}},"
                )
            else:
                rows.append(
                    f'    {{"{n}", (getter){tname}_get_{n}, '
                    f"NULL, NULL, NULL}},"
                )

    table_name = f"{tname}_getset"
    table = (
        f"static PyGetSetDef {table_name}[] = {{\n"
        + "\n".join(rows)
        + ("\n" if rows else "")
        + "    {NULL, NULL, NULL, NULL, NULL}\n};\n"
    )
    return "\n".join(funcs) + "\n" + table, table_name


def _cache_fetch(cfg: dict, module: str) -> str:
    """The ``tp_init`` body resolving every ``cache = true`` getter into the
    object's stashed struct (fixed metadata read once)."""
    out = []
    for gi, g in enumerate(C.handle_getters(cfg, module)):
        if g.get("cache"):
            if _scalar_out(g):
                out.append(f"    self->_g{gi} = {g['fn']}(self->h);")
            else:
                out.append(f"    {g['fn']}(self->h, &self->_g{gi});")
    return "\n".join(out)


# ── the whole type (struct + dealloc + close + context-manager) ───────────────


def render_type(cfg: dict, module: str) -> str:
    """Emit the handle ``PyTypeObject`` — struct, tp_init, methods, decoded
    getsets, and the RAII protocol (idempotent ``close`` + context-manager +
    ``tp_dealloc``). The close / enter / exit / dealloc shape is reused verbatim
    from the composer."""
    tname = C.handle_type_name(cfg, module)
    obj = f"{tname}Object"
    type_obj = f"{tname}Type"
    htype = C.handle_type(cfg, module)
    close_fn = C.handle_close_fn(cfg, module)
    pkg = C.project_name(cfg)
    pkg_path = C.handle_package(cfg, module) or C.module_paths(module).pypath
    dotted = f"{pkg}.{pkg_path.replace('/', '.')}.{tname}"

    # struct: opaque handle + closed flag + stashed init scalars (expr refs) +
    # one cached out-struct per cache=true getter.
    stash_fields = "".join(
        f"    {ct} {n};\n" for n, ct in _stash_inits(cfg, module)
    )
    cache_fields = "".join(
        f"    {g['out']} _g{gi};\n"
        for gi, g in enumerate(C.handle_getters(cfg, module))
        if g.get("cache")
    )
    struct = f"""typedef struct {{
    PyObject_HEAD
    {htype} *h;
    int       closed;
{stash_fields}{cache_fields}}} {obj};
"""

    # RAII — idempotent close + context-manager + dealloc (composer pattern).
    # For an init-in-place handle (gh-315) jm owns the malloc, so it free()s the
    # struct (after an optional close_fn that finalizes owned members); a
    # create_fn handle is released by close_fn alone.
    init_fn = C.handle_init_fn(cfg, module)
    if init_fn:
        finalize = f"{close_fn}(self->h); " if close_fn else ""
        destroy = f"{finalize}free(self->h);"
    else:
        destroy = f"{close_fn}(self->h);"

    close = f"""static PyObject *
{tname}_close({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if (!self->closed && self->h) {{
        {destroy}
        self->closed = 1;
    }}
    Py_RETURN_NONE;
}}
"""
    ctx = ""
    ctx_rows = ""
    if C.handle_context(cfg, module):
        ctx = f"""static PyObject *
{tname}_enter({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    Py_INCREF(self);
    return (PyObject *)self;
}}

static PyObject *
{tname}_exit({obj} *self, PyObject *args)
{{
    (void)args;
    return {tname}_close(self, NULL);
}}
"""
        ctx_rows = (
            f'    {{"__enter__", (PyCFunction){tname}_enter, METH_NOARGS, NULL}},\n'
            f'    {{"__exit__", (PyCFunction){tname}_exit, METH_VARARGS, NULL}},\n'
        )

    dealloc = f"""static void
{tname}_dealloc({obj} *self)
{{
    if (!self->closed && self->h) {{
        {destroy}
    }}
    Py_TYPE(self)->tp_free((PyObject *)self);
}}
"""

    methods = "\n".join(
        _emit_method(cfg, module, m) for m in C.handle_methods(cfg, module)
    )
    getsets, getset_name = render_getsets(cfg, module)

    method_rows = []
    for m in C.handle_methods(cfg, module):
        flags = (
            "METH_VARARGS | METH_KEYWORDS"
            if _method_kwargs(m)
            else "METH_VARARGS"
        )
        method_rows.append(
            f'    {{"{m["name"]}", (PyCFunction){tname}_{m["name"]}, '
            f"{flags}, NULL}},"
        )
    method_rows.append(
        f'    {{"close", (PyCFunction){tname}_close, METH_NOARGS, '
        '"close() -> None"},'
    )
    method_table = "\n".join(method_rows)

    return f"""{struct}
{render_tp_init(cfg, module)}
{methods}
{getsets}
{close}
{ctx}{dealloc}
static PyMethodDef {tname}_methods[] = {{
{method_table}
{ctx_rows}    {{NULL, NULL, 0, NULL}}
}};

static PyTypeObject {type_obj} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{dotted}",
    .tp_basicsize = sizeof({obj}),
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_new       = PyType_GenericNew,
    .tp_init      = (initproc){tname}_init,
    .tp_dealloc   = (destructor){tname}_dealloc,
    .tp_getset    = {getset_name},
    .tp_methods   = {tname}_methods,
    .tp_doc       = PyDoc_STR("{tname} — handle over `{C.handle_backing(cfg, module)}`."),
}};
"""


# ── whole-file assembly (mirrors _capsule.render_ext) ─────────────────────────


def render_ext(cfg: dict, module: str) -> str:
    """Render the full ``<module>_ext.c`` for a handle module (gh-306)."""
    backing = C.handle_backing(cfg, module)
    tname = C.handle_type_name(cfg, module)
    header = C.handle_header(cfg, module) or f"{backing}/{backing}_core.h"
    mp = C.module_paths(module)
    leaf = mp.leaf
    opt_backend = C.handle_optional_backend(cfg, module)

    # The optional backend is declared a weak extern so its absence is a NULL
    # symbol at link time rather than an unresolved reference (the guard checks
    # it). GCC/Clang honour __attribute__((weak)) on a forward declaration.
    weak_decl = ""
    if opt_backend:
        weak_decl = (
            "/* Optional backend: weak so a platform without it links with the\n"
            " * symbol resolving to NULL; tp_init raises NotImplementedError. */\n"
            f"extern __typeof__({opt_backend}) {opt_backend} "
            "__attribute__((weak));\n"
        )

    parts = [
        f"""/*
 * {mp.cname}_ext.c — handle extension: typed `{tname}` over `{backing}` (jm; gh-306).
 *
 * `{tname}` wraps an opaque {C.handle_type(cfg, module)} *; the resource logic
 * lives hand-written in the backing _core.c. This file is pure generated glue —
 * lifecycle, arg coercion, numpy marshaling, decoded-getter properties, RAII.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "{header}"
{weak_decl}""",
        render_enum_tables(cfg, module),
        render_type(cfg, module),
    ]

    parts.append(f"""static struct PyModuleDef _moduledef = {{
    PyModuleDef_HEAD_INIT, "{leaf}", NULL, -1, NULL,
    NULL, NULL, NULL, NULL
}};

PyMODINIT_FUNC
PyInit_{leaf}(void)
{{
    import_array();
    if (PyType_Ready(&{tname}Type) < 0) return NULL;
    PyObject *m = PyModule_Create(&_moduledef);
    if (!m) return NULL;
    Py_INCREF(&{tname}Type);
    if (PyModule_AddObject(m, "{tname}", (PyObject *)&{tname}Type) < 0) {{
        Py_DECREF(&{tname}Type);
        Py_DECREF(m);
        return NULL;
    }}
    return m;
}}
""")
    return "\n".join(parts)


# ── CMakeLists.txt (mirrors _capsule.render_cmake) ────────────────────────────


def render_cmake(cfg: dict, module: str) -> str:
    """Render the handle module's ``CMakeLists.txt`` — one Python-extension
    target linking the ``link = true`` dependency cores + ``extra_link_libs``,
    dropping the ``.so`` into the (optionally overridden) package directory.
    Mirrors the capsule CMake generator exactly."""
    mp = C.module_paths(module)
    leaf, cname = mp.leaf, mp.cname
    out_pkg = C.handle_package(cfg, module) or mp.pypath

    link_cores = C.dep_link_libs(C.handle_depends_on(cfg, module))
    extra = C.handle_extra_link_libs(cfg, module)
    link_lines = "".join(f"    {lib}\n" for lib in link_cores + extra)

    return f"""if(BUILD_PYTHON)

# {cname} — handle extension: typed `{C.handle_type_name(cfg, module)}` over \
`{C.handle_backing(cfg, module)}` (gh-306).
# The resource logic lives in the backing _core.c; this is pure generated glue.
# Generated by just-makeit from [module.{module}] — edit the manifest, not this.
Python3_add_library({leaf} MODULE WITH_SOABI {cname}_ext.c)
target_link_libraries({leaf} PRIVATE
{link_lines}    Python3::NumPy)
target_include_directories({leaf} PRIVATE ${{CMAKE_SOURCE_DIR}}/native/inc)
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
"""


# ── .pyi type stub (mirrors _capsule.render_pyi, class-shaped) ────────────────


def _pyi_scalar(ctype: str) -> str:
    """Python annotation for a scalar C arg / field type."""
    return _capsule._pyi_scalar(ctype)


def render_pyi(cfg: dict, module: str) -> str:
    """Render a thin class-shaped ``<leaf>.pyi`` for a handle module.

    The class, its ``__init__`` signature (from ``create_args``), each method,
    each decoded-getter property, and (when enabled) ``__enter__`` / ``__exit__``
    / ``close``. Signatures only — header-derived docstrings are a follow-up,
    same as the capsule stub."""
    tname = C.handle_type_name(cfg, module)
    C.handle_backing(cfg, module)
    mp = C.module_paths(module)

    lines = [
        f"# {mp.leaf}.pyi — type stubs for the {module} handle extension.",
        "#",
        f"# Generated by just-makeit (gh-306). `{tname}` is a typed CPython",
        f"# class over an opaque {C.handle_type(cfg, module)} resource handle.",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "import numpy as np",
        "from numpy.typing import NDArray",
        "",
        f"class {tname}:",
    ]

    # __init__ from create_args.
    init_params = []
    for a in C.handle_create_args(cfg, module):
        n = a["name"]
        if a.get("type") == "path":
            ann = "str"
        elif a.get("enum"):
            ann = "str"
        else:
            ann = _pyi_scalar(a["type"])
        if "default" in a or a.get("kwonly"):
            init_params.append(f"{n}: {ann} = ...")
        else:
            init_params.append(f"{n}: {ann}")
    ip = ", ".join(init_params)
    lines.append(f"    def __init__(self, {ip}) -> None: ...")

    # methods — one of the four shapes (a)-(d) (see _emit_method).
    for m in C.handle_methods(cfg, module):
        name = m["name"]
        margs = list(m.get("args", []))
        returns = m.get("returns")
        arrays = [a for a in margs if str(a.get("type", "")).endswith("[]")]
        writable_out = [a for a in arrays if a.get("writable")]
        scalars = [a for a in margs if a not in arrays]
        ret_arr = bool(returns) and str(returns).endswith("[]")
        if writable_out and ret_arr:
            # (d) execute(x, out) -> ndarray
            sig = "self, x: NDArray[Any], out: NDArray[Any]"
            ann = "NDArray[Any]"
        elif ret_arr and not arrays:
            # (c) read(n) -> ndarray
            sig = "self, n: int"
            ann = "NDArray[Any]"
        elif arrays:
            # (b) x[, scalars] -> scalar / None
            parts = ["self", "x: NDArray[Any]"] + [
                f"{s['name']}: {_pyi_scalar(s['type'])}" for s in scalars
            ]
            sig = ", ".join(parts)
            ann = _pyi_scalar(returns) if returns else "None"
        elif margs:
            # (a) scalars -> scalar / None; a `default` shows as `= ...` (#319).
            sig = "self, " + ", ".join(
                f"{a['name']}: {_pyi_scalar(a['type'])}"
                + (" = ..." if a.get("default") is not None else "")
                for a in margs
            )
            ann = _pyi_scalar(returns) if returns else "None"
        else:
            sig = "self"
            ann = _pyi_scalar(returns) if returns else "None"
        lines.append(f"    def {name}({sig}) -> {ann}: ...")

    # decoded-getter properties (a writable_fn field also gets a setter).
    for g in C.handle_getters(cfg, module):
        for f in g.get("fields", []):
            if f.get("enum"):
                ann = "str"
            else:
                ann = _pyi_scalar(f["type"])
            lines.append("    @property")
            lines.append(f"    def {f['name']}(self) -> {ann}: ...")
            if f.get("writable_fn"):
                lines.append(f"    @{f['name']}.setter")
                lines.append(
                    f"    def {f['name']}(self, value: {ann}) -> None: ..."
                )

    # RAII surface.
    lines.append("    def close(self) -> None: ...")
    if C.handle_context(cfg, module):
        lines.append(f"    def __enter__(self) -> {tname}: ...")
        lines.append("    def __exit__(self, *exc: Any) -> None: ...")
    lines.append("")
    return "\n".join(lines)


# ── materialization (driven by jm apply's _replay; mirrors _capsule) ──────────


def materialize(cfg: dict, root: Path, module: str) -> None:
    """Write a handle module's generated files into *root* (a project tree).

    Emits the binding ``<cname>_ext.c``, the module ``CMakeLists.txt``, and the
    ``.pyi`` stub, then wires ``add_subdirectory`` into the top ``CMakeLists.txt``
    under the ``# ── Modules`` sentinel. Mirrors ``_capsule.materialize`` — the
    handle shape has no ``_core`` / per-object scaffolding either."""
    from ._init import _write

    pkg = C.project_name(cfg)
    mp = C.module_paths(module)
    out_pkg = C.handle_package(cfg, module) or mp.pypath

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

    # Wire the top CMakeLists add_subdirectory (Modules sentinel), like
    # _capsule.materialize does.
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
