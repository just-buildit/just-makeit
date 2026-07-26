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
from typing import TYPE_CHECKING

from . import _capsule
from . import _coerce
from . import _composer
from . import _config as C
from . import _context as Ctx
from . import _types as T

if TYPE_CHECKING:
    from ._docstring import DoxyBlock

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
        if a.get("type") in ("path", "string"):
            continue  # not a stashable scalar
        if f"self->{n}" in exprs:
            ctype = "int" if a.get("enum") else a["type"]
            out.append((n, ctype))
    return out


# ── tp_init (opaque create → handle; mirrors capsule create + composer init) ──


def _arg_decl(a: dict) -> str:
    """C local decl for one create-arg, with its manifest default."""
    n = a["name"]
    if a.get("type") == "path":
        return "    " + _coerce.path_decl(n)
    if a.get("type") == "bytes":
        # gh-565: an opaque blob -> (const void *, size_t) via y#. Two locals;
        # no release (y# borrows the buffer for the call's duration).
        return "\n".join("    " + ln for ln in _coerce.bytes_decl(n))
    if a.get("type") == "string":
        # A borrowed const char * from PyArg "s" (NUL-terminated UTF-8). Like a
        # path but NOT an fspath — an in-memory string (e.g. a JSON spec). The
        # create_fn MUST copy/consume it before returning (borrowed, gh-219).
        return f"    const char *{n} = NULL;"
    if a.get("enum"):
        default = a.get("default", "")
        return f'    const char *{n} = "{default}";'
    default = a.get("default", "0")
    return f"    {a['type']} {n} = {default};"


def _arg_fmt(a: dict) -> str:
    """PyArg_ParseTupleAndKeywords format char for one create-arg."""
    if a.get("type") == "path":
        return _coerce.path_fmt()
    if a.get("type") == "bytes":
        return _coerce.bytes_fmt()  # gh-565: y#
    if a.get("enum") or a.get("type") == "string":
        return "s"
    return _scalar_fmt(a["type"])


def _arg_addr(a: dict) -> str:
    """The ``&target`` (or converter+target) PyArg address fragment."""
    n = a["name"]
    if a.get("type") == "path":
        return _coerce.path_addr(n)
    if a.get("type") == "bytes":
        return _coerce.bytes_addr(n)  # gh-565: &n, &n_len
    return f"&{n}"


def _create_call_arg(a: dict) -> str:
    """The expression passed to ``create_fn`` for one create-arg."""
    n = a["name"]
    if a.get("type") == "path":
        return _coerce.path_call_expr(n)
    if a.get("type") == "bytes":
        return _coerce.bytes_call_exprs(n)  # gh-565: two args (ptr, len)
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
    # gh-178 review #6: a no-default create-arg is REQUIRED — it goes before the
    # `|`, not after. The old all-optional `|...` let `ZmqSink()` parse with a
    # NULL endpoint and crash. An arg is optional iff it carries a manifest
    # `default` (mirrors the module-fn / shape-(a) split). Required args must
    # precede optional ones in the manifest, same as a Python signature.
    fmt_parts: list[str] = []
    opt_started = False
    for a in args:
        if a.get("default") is not None and not opt_started:
            fmt_parts.append("|")
            opt_started = True
        fmt_parts.append(_arg_fmt(a))
    fmt = "".join(fmt_parts)
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
    fs_decref = "".join(
        f"    {_coerce.path_release(a['name'])}\n" for a in fs_args
    )

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

    # gh-178 review #9: a second __init__() call on a live object would overwrite
    # self->h and leak the prior handle. Release it first (mirrors tp_dealloc)
    # now that args parsed and validated, i.e. we are committed to rebuilding.
    # On the first init the struct is zeroed (tp_new), so this is a no-op.
    reinit = f"""    if (!self->closed && self->h) {{
        {_destroy_silent(cfg, module)}
        self->h = NULL;
        self->closed = 1;
    }}
"""

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
        # gh-514: honour this module's declared create_error/create_error_message
        # instead of hardcoding RuntimeError. A handle module is the shape that
        # opens external resources — files, sockets, devices — so an open that
        # fails for a genuinely user-actionable reason (missing file, unknown
        # container, refused format) deserves better than a message naming an
        # internal C symbol. Undeclared keeps the historical text byte-for-byte.
        #
        # The path borrows are released by fs_decref above (gh-219: after
        # create_fn copied them), so this failure branch has nothing to free.
        _err_cat = C.handle_create_error(cfg, module)
        if _err_cat and _err_cat not in C.ERROR_CATEGORIES:
            supported = ", ".join(sorted(C.ERROR_CATEGORIES))
            raise ValueError(
                f"module '{module}': create_error '{_err_cat}' is not a"
                f" recognised exception. Supported: {supported}."
            )
        _fail_block = Ctx.make_errors_ctx(
            module,
            _err_cat,
            C.handle_create_error_message(cfg, module),
            create_fn=create_fn,
            handle_expr="self->h",
            undeclared_body=(
                f'        PyErr_SetString(PyExc_RuntimeError, "{create_fn}'
                ' failed");\n'
            ),
        )["create_fail_block"].rstrip("\n")
        construct = f"""    self->h = {create_fn}({call_args});
{fs_decref}{_fail_block}"""

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
{reinit}{construct}
    self->closed = 0;
{stash_s}
{post_s}
{cache_s}
    return 0;
}}
"""


def _destroy_silent(cfg: dict, module: str) -> str:
    """The teardown statement releasing a live handle — ``close_fn(self->h)``
    then, for an init-in-place handle (gh-315), ``free(self->h)``.

    The ``close_fn`` return value is discarded here: this drives ``tp_dealloc``
    and the re-``__init__`` teardown (gh-178 review #9), neither of which may
    raise. The status-checking variant lives inline in ``close()``
    (:func:`render_type`, gh-178 review #5)."""
    close_fn = C.handle_close_fn(cfg, module)
    if C.handle_init_fn(cfg, module):
        finalize = f"{close_fn}(self->h); " if close_fn else ""
        return f"{finalize}free(self->h);"
    return f"{close_fn}(self->h);"


def _init_fsfree(args: list[dict]) -> str:
    """Free path borrows on an enum-validation error inside tp_init."""
    fs = [a for a in args if a.get("type") == "path"]
    if not fs:
        return ""
    return "\n" + "".join(
        f"        {_coerce.path_release(a['name'])}" for a in fs
    )


# ── tp_methods (scalar / array-in / int-in→array-out; reuses capsule numpy) ───


def _method_kwargs(m: dict) -> bool:
    """True if a method parses with ``PyArg_ParseTupleAndKeywords`` and so
    registers as ``METH_VARARGS | METH_KEYWORDS`` (the 3-arg C signature).

    Mirrors the shape dispatch in :func:`_emit_method`:
      (a) scalar args → keywords (``track_clipping(on=True)``, #319);
      (b) array-in **with** trailing scalars → keywords (so ``send(iq, fs,
          fc=…)`` keeps its default, gh-178 review #6); a bare ``write(iq)``
          stays positional;
      (c) int-in → array-out and (d) array-in + writable-out → positional.
    A no-arg method is a plain ``METH_VARARGS`` stub."""
    margs = m.get("args", [])
    if not margs:
        return False
    returns = m.get("returns")
    if returns == "bytes":
        return (
            False  # (f) bytes-out: positional, like the array out_len_fn shape
        )
    arrays = [a for a in margs if str(a.get("type", "")).endswith("[]")]
    ret_arr = bool(returns) and str(returns).endswith("[]")
    if ret_arr and not arrays:
        return False  # (c) int-in -> array-out
    if any(a.get("writable") for a in arrays) and ret_arr:
        return False  # (d) array-in + writable array-out
    if arrays:
        return len(arrays) != len(margs)  # (b) only when trailing scalars
    return True  # (a) scalar args


def _scalar_string_argparse(margs: list[dict]) -> tuple[str, str, list[str]]:
    """Positional parse of a handle method's scalar/``string`` args.

    Shared by the two ``out_len_fn`` shapes — (e) array-out and the (f)
    bytes-out below — so the arg marshaling can't drift between them. Returns
    ``(decls, parse, calls)``: C local declarations, the ``PyArg_ParseTuple``
    block (``(void)args;`` when there are none), and the call-through
    expressions (``string`` -> ``const char *``; a scalar -> its safe-width
    ``parse_type`` narrowed by ``to_c``)."""
    decls, fmt_parts, addrs, calls = "", [], [], []
    for a in margs:
        an = a["name"]
        if a.get("type") == "string":
            decls += f"    const char *{an} = NULL;\n"
            fmt_parts.append("s")
            addrs.append(f"&{an}")
            calls.append(an)
        else:
            meta = T._CTYPE_META[a["type"]]
            pt = meta.get("parse_type", a["type"])  # safe-width parse target
            to_c = meta.get("to_c")
            decls += f"    {pt} {an}_raw = 0;\n"
            fmt_parts.append(meta["fmt"])
            addrs.append(f"&{an}_raw")
            calls.append(to_c(an) if to_c else f"{an}_raw")
    parse = (
        f'    if (!PyArg_ParseTuple(args, "{"".join(fmt_parts)}", '
        f"{', '.join(addrs)}))\n        return NULL;\n"
        if margs
        else "    (void)args;\n"
    )
    return decls, parse, calls


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

    # (e) scalar/string args → HANDLE-length array-out. When a method returns an
    # array, takes no input array, and declares `out_len_fn`, the output length
    # comes from the handle (not from an arg): allocate `out_len_fn(self->h)`,
    # parse the declared args (scalars and/or a `string`), call
    # `fn(self->h, args…, out)`, and trim to the returned count (an independent
    # numpy-owned array — gh-219). Serves both `render(overrides_json)->cf32[]`
    # (one string arg) and the scalar fast-path `at(snr, seed)->cf32[]`.
    # Positional (METH_VARARGS) — _method_kwargs() returns False for it.
    out_len_fn = m.get("out_len_fn")
    if returns == "bytes" and not out_len_fn:
        raise ValueError(
            f"handle method '{name}': returns = \"bytes\" requires an"
            " 'out_len_fn' (a `size_t <fn>(const <handle>*)` that sizes the"
            " blob before it is filled)."
        )
    if returns and str(returns).endswith("[]") and not array_in and out_len_fn:
        out_elem, out_npy = _array_elem_npy(returns)
        decls, parse, calls = _scalar_string_argparse(margs)
        call_args = "".join(f", {c}" for c in calls)
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
{decls}{parse}{closed_guard}
    npy_intp _n = (npy_intp){out_len_fn}(self->h);
    PyObject *arr = PyArray_SimpleNew(1, &_n, {out_npy});
    if (!arr) return NULL;
    {out_elem} *_out = ({out_elem} *)PyArray_DATA((PyArrayObject *)arr);
    size_t _got;
{gil_open}    _got = {fn}(self->h{call_args}, _out);
{gil_close}    PyArray_DIMS((PyArrayObject *)arr)[0] = (npy_intp)_got; /* trim */
    return arr;
}}
"""

    # (f) scalar/string args → HANDLE-length `bytes` (gh-565). The write half of
    # Plan save/restore: `size_t save_bytes(const h*)` sizes the blob, a temp
    # buffer is filled by `size_t save(const h*, args…, void *out)`, and the
    # result is COPIED into an immutable `bytes` — no aliasing, so none of the
    # array shapes' deferred-free / view machinery applies. Positional
    # (METH_VARARGS) — _method_kwargs() returns False for a bytes return.
    if returns == "bytes" and out_len_fn:
        decls, parse, calls = _scalar_string_argparse(margs)
        call_args = "".join(f", {c}" for c in calls)
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
{decls}{parse}{closed_guard}
    size_t _n = (size_t){out_len_fn}(self->h);
    char *_buf = (char *)PyMem_Malloc(_n ? _n : 1);
    if (!_buf) return PyErr_NoMemory();
    size_t _got;
{gil_open}    _got = {fn}(self->h{call_args}, _buf);
{gil_close}    PyObject *_r = PyBytes_FromStringAndSize(_buf, (Py_ssize_t)_got);
    PyMem_Free(_buf);
    return _r;
}}
"""

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
        _out_guard = _coerce.out_buffer_guard(
            f"{on}_obj", out_npy, label=on, decrefs=f"Py_DECREF({xn}_arr);"
        )
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
    PyObject *{xn}_obj, *{on}_obj;
    if (!PyArg_ParseTuple(args, "OO", &{xn}_obj, &{on}_obj)) return NULL;
{closed_guard}
    PyArrayObject *{xn}_arr = (PyArrayObject *)PyArray_FROM_OTF(
        {xn}_obj, {in_npy}, NPY_ARRAY_C_CONTIGUOUS);
    if (!{xn}_arr) return NULL;

{_out_guard}    PyArrayObject *{on}_arr = (PyArrayObject *)PyArray_FROM_OTF(
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
        scal_call = "".join(f", {s['name']}" for s in others)
        ret_to_py = (
            _to_py(returns, "r")
            if returns
            else "(Py_INCREF(Py_None), Py_None)"
        )
        ret_decl = f"    {returns} r;\n" if returns else ""
        assign = "r = " if returns else ""
        body_tail = f"""{closed_guard}
    PyArrayObject *x_arr = (PyArrayObject *)PyArray_FROM_OTF(
        x_obj, {in_npy}, NPY_ARRAY_C_CONTIGUOUS);
    if (!x_arr) return NULL;
    size_t n_in = (size_t)PyArray_SIZE(x_arr);
    const {in_elem} *in_data = (const {in_elem} *)PyArray_DATA(x_arr);
{ret_decl}{gil_open}    {assign}{fn}(self->h, in_data, n_in{scal_call});
{gil_close}    Py_DECREF(x_arr);
    return {ret_to_py};"""
        # Trailing scalars parse with keywords + defaults (gh-178 review #6: the
        # hand-written send(iq, fs, fc=…) had an fc default the old positional
        # PyArg_ParseTuple dropped); a `default` inserts the `|` optional split,
        # same as shape (a). With no trailing scalars it stays positional `O`.
        if others:
            scal_decls = ""
            fmt_parts = ["O"]
            inserted = False
            for s in others:
                d = s.get("default")
                init = f" = {d}" if d is not None else ""
                scal_decls += f"    {s['type']} {s['name']}{init};\n"
                if d is not None and not inserted:
                    fmt_parts.append("|")
                    inserted = True
                fmt_parts.append(_scalar_fmt(s["type"]))
            fmt_b = "".join(fmt_parts)
            kwnames = [a["name"]] + [s["name"] for s in others]
            kwlist_b = ", ".join(f'"{n}"' for n in kwnames)
            scal_addrs = "".join(f", &{s['name']}" for s in others)
            return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{{kwlist_b}, NULL}};
    PyObject *x_obj;
{scal_decls}    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt_b}", kwlist,
            &x_obj{scal_addrs}))
        return NULL;
{body_tail}
}}
"""
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args)
{{
    PyObject *x_obj;
    if (!PyArg_ParseTuple(args, "O", &x_obj)) return NULL;
{body_tail}
}}
"""

    # (a) scalar / path args → scalar / None / status-raise. With args, support
    # keyword passing + defaults (#319) via PyArg_ParseTupleAndKeywords (mirrors
    # tp_init and module functions); a no-arg method stays a plain METH_VARARGS
    # stub. A `path` arg (gh-565) crosses as a borrowed `const char *` via the
    # ctor's `_arg_*` coercion, released after the call (gh-219). An `error =
    # "<category>"` (gh-565) marks an `int`-returning method a status check: the
    # method returns None and raises the declared exception on a non-zero rc,
    # the handle-method mirror of the object side's `status_return` (gh-432) and
    # of `wfm_writer.destroy`'s fallible close (gh-541).
    err_cat = m.get("error")
    if err_cat and err_cat not in C.ERROR_CATEGORIES:
        supported = ", ".join(sorted(C.ERROR_CATEGORIES))
        raise ValueError(
            f"handle method '{name}': error '{err_cat}' is not a recognised"
            f" exception. Supported: {supported}."
        )
    if err_cat and not returns:
        raise ValueError(
            f"handle method '{name}': error requires an `int` status return"
            ' (declare returns = "int").'
        )
    call = ", ".join(["self->h"] + [_create_call_arg(a) for a in margs])
    # path borrows are released only AFTER the C call has copied them (gh-219).
    fs_release = "".join(
        f"    {_coerce.path_release(a['name'])}\n"
        for a in margs
        if a.get("type") == "path"
    )
    if err_cat:
        ret = f"""    {returns} _rc;
{gil_open}    _rc = {fn}({call});
{gil_close}{fs_release}    if (_rc != 0) {{
        PyErr_Format(PyExc_{err_cat}, "{fn} failed (rc=%d)", (int)_rc);
        return NULL;
    }}
    Py_RETURN_NONE;"""
    elif returns:
        ret = f"""    {returns} r;
{gil_open}    r = {fn}({call});
{gil_close}{fs_release}    return {_to_py(returns, "r")};"""
    else:
        ret = f"""{gil_open}    {fn}({call});
{gil_close}{fs_release}    Py_RETURN_NONE;"""

    if margs:
        decls = ""
        fmt_parts = []
        addrs_list = []
        inserted = False
        for a in margs:
            if a.get("type") == "path":
                # gh-565: the ctor's fspath coercion (O& + FSConverter), a
                # required positional — no `default`, so it never trips the `|`.
                decls += _arg_decl(a) + "\n"
                fmt_parts.append(_arg_fmt(a))
                addrs_list.append(_arg_addr(a))
                continue
            d = a.get("default")
            init = f" = {d}" if d is not None else ""
            decls += f"    {a['type']} {a['name']}{init};\n"
            if d is not None and not inserted:
                fmt_parts.append("|")  # everything after is optional
                inserted = True
            fmt_parts.append(_scalar_fmt(a["type"]))
            addrs_list.append(f"&{a['name']}")
        fmt = "".join(fmt_parts)
        kwlist = ", ".join(f'"{a["name"]}"' for a in margs)
        addrs = ", ".join(addrs_list)
        # gh-219: release any path borrow if PyArg fails after its converter ran.
        fs_fail = "".join(
            f"        {_coerce.path_release(a['name'])}\n"
            for a in margs
            if a.get("type") == "path"
        )
        parse_fail = (
            f" {{\n{fs_fail}        return NULL;\n    }}"
            if fs_fail
            else " return NULL;"
        )
        return f"""static PyObject *
{tname}_{name}({obj} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{{kwlist}, NULL}};
{decls}    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}", kwlist,
            {addrs})){parse_fail}
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


# ── module-level factories (alternate constructors — gh-565) ──────────────────


def _emit_factory(cfg: dict, module: str, f: dict) -> str:
    """Emit one module-level factory function — an alternate constructor.

    ``PlanFromBlob(blob) -> Plan``: parse the factory's ``init_params`` (a
    ``bytes`` blob / ``path`` / scalars, via the same ``_arg_*`` coercion the
    ctor uses), call ``create_fn`` to build a FRESH handle, wrap it in a
    tp_alloc'd instance of the module's type (bypassing ``tp_init`` — a restore
    is not the primary ctor), and return it. The created object is named
    ``self`` so :func:`_cache_fetch` / the destroy helpers apply verbatim.

    A restore has no primary-ctor arguments, so the expr-stashed init scalars
    (:func:`_stash_inits`) stay zero — a documented limitation, since the blob
    reconstructs the handle wholesale and those Python-side values are simply
    unavailable. ``cache = true`` getters ARE resolved (they read the rebuilt
    handle, not the stashed inits)."""
    tname = C.handle_type_name(cfg, module)
    obj = f"{tname}Object"
    htype = C.handle_type(cfg, module)
    mp = C.module_paths(module)
    fname = f["name"]
    create_fn = f["create_fn"]
    ips = list(f.get("init_params", []))

    decls = "\n".join(_arg_decl(a) for a in ips)
    fmt = "".join(_arg_fmt(a) for a in ips)
    addrs = ", ".join(_arg_addr(a) for a in ips)
    call_args = ", ".join(_create_call_arg(a) for a in ips)
    parse = (
        f'    if (!PyArg_ParseTuple(args, "{fmt}", {addrs}))\n'
        "        return NULL;\n"
        if ips
        else "    (void)args;\n"
    )
    # path borrows are released only AFTER create_fn copies them (gh-219);
    # bytes (y#) borrows need no release (valid for the call's duration).
    fs_decref = "".join(
        f"    {_coerce.path_release(a['name'])}\n"
        for a in ips
        if a.get("type") == "path"
    )
    # Alloc-failure cleanup destroys the just-built handle directly (self is
    # not yet allocated, so the self->h teardown helpers do not apply here).
    close_fn = C.handle_close_fn(cfg, module)
    free_h = "free(_h); " if C.handle_init_fn(cfg, module) else ""
    destroy_h = (f"{close_fn}(_h); " if close_fn else "") + free_h
    cache = _cache_fetch(cfg, module)
    cache_block = f"{cache}\n" if cache else ""
    return f"""static PyObject *
{mp.leaf}_{fname}(PyObject *_mod, PyObject *args)
{{
    (void)_mod;
{decls}
{parse}    {htype} *_h = {create_fn}({call_args});
{fs_decref}    if (!_h) {{
        PyErr_SetString(PyExc_ValueError, "{fname} failed");
        return NULL;
    }}
    {obj} *self = ({obj} *){tname}Type.tp_alloc(&{tname}Type, 0);
    if (!self) {{
        {destroy_h}return NULL;
    }}
    self->h = _h;
    self->closed = 0;
{cache_block}    return (PyObject *)self;
}}
"""


def render_factories(cfg: dict, module: str) -> str:
    """All factory functions for *module* (empty when none are declared)."""
    return "\n".join(
        _emit_factory(cfg, module, f) for f in C.handle_factories(cfg, module)
    )


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


def _decode_field_stmts(f: dict, scalar: bool, n_choices: int) -> str:
    """Statements ending in a ``return`` that decode one field (gh-521).

    Every non-enum transform is the historical one-line
    ``return <expr>;`` — byte-identical to what :func:`_decode_field` produced
    inline before this split.

    An ``enum`` field is range-checked first. A handle module wraps an
    external resource, so its enum-valued fields are typically decoded from
    data the process does not control — gh-514's motivating case was a Midas
    BLUE header's format mode designator, where unsupported modes provably
    occur in real files. Indexing the SSOT table blind therefore read past its
    end on any code outside the table: at exactly ``n_choices`` that is the
    NULL terminator (``PyUnicode_FromString(NULL)``), and beyond it arbitrary
    memory. In a built extension this **segfaulted** the interpreter rather
    than raising. The check turns an unsupported code into a ValueError the
    caller can act on, which is the same distinction gh-514 was about.

    ``n_choices`` is the enum's length from the ``[[enum]]`` SSOT; a
    non-positive value means the enum could not be resolved, in which case the
    unchecked form is kept rather than emitting a check that rejects
    everything.
    """
    if not f.get("enum") or f.get("expr") or n_choices <= 0:
        return f"    return {_decode_field(f, scalar)};"
    acc = "tmp" if scalar else f"tmp.{f.get('from', f['name'])}"
    return (
        f"    long _v = (long)({acc});\n"
        f"    if (_v < 0 || _v >= {n_choices}) {{\n"
        f"        PyErr_Format(PyExc_ValueError,\n"
        f'            "{f["name"]} holds out-of-range {f["enum"]} value %ld"\n'
        f'            " (valid: 0..{n_choices - 1})", _v);\n'
        f"        return NULL;\n"
        f"    }}\n"
        f"    return PyUnicode_FromString(_enum_{f['enum']}[_v]);"
    )


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
    # gh-521: the SSOT registry supplies each enum's length for the decode
    # range check below.
    _enum_reg = C.enums(cfg)

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
                # `tmp` is typed by the GETTER's return type (`returns`), not the
                # field's decoded type — so a derived `expr` whose result type
                # differs from the accessor's return (a bool `clipped` over a
                # double peak) keeps full precision and the right C type (gh-326).
                #
                # gh-333: when the field has an `expr`, the getter's return type
                # is (almost always) NOT the decoded `type` — defaulting `tmp` to
                # `type` would truncate the getter's value BEFORE the expr runs,
                # silently (it compiles since gh-330 added <stdbool.h>). Require
                # `returns` for an expr field rather than guess.
                if f.get("expr") and "returns" not in f:
                    raise ValueError(
                        f"handle '{C.handle_type_name(cfg, module)}': "
                        f"per-field getter '{f['name']}' has an `expr` but no "
                        f"`returns` — declare the C return type of getter "
                        f"'{field_getter}' (the expr operates on `tmp` of that "
                        f"type, then decodes to '{f['type']}'). Without it the "
                        f"getter value is silently truncated to '{f['type']}' "
                        f"before the expr."
                    )
                gtype = f.get("returns", f["type"])
                fetch = f"""    {gtype} tmp;
{closed_get}
    tmp = {field_getter}(self->h);"""
                f_scalar = True
            else:
                fetch = table_fetch
                f_scalar = scalar
            # gh-521: the enum decode is range-checked, so the body is built
            # as statements rather than a single return expression.
            _n_choices = len(_enum_reg.get(f.get("enum") or "", ()))
            funcs.append(f"""static PyObject *
{tname}_get_{n}({obj} *self, void *closure)
{{
    (void)closure;
{fetch}
{_decode_field_stmts(f, f_scalar, _n_choices)}
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
    destroy = _destroy_silent(cfg, module)

    # gh-178 review #5: when close_fn reports a status code (close_returns set),
    # close() captures it and raises RuntimeError on a non-zero result — the
    # hand-written close did (wfm_writer_close patches the BLUE data_size and can
    # fail on a short write). The handle is still torn down and marked closed
    # before raising (one-shot), so the error never double-closes. tp_dealloc
    # keeps the silent teardown — a destructor must not raise.
    close_ret = C.handle_close_returns(cfg, module)
    if close_ret:
        free_s = "        free(self->h);\n" if init_fn else ""
        close_body = f"""        {close_ret} _rc = {close_fn}(self->h);
{free_s}        self->h = NULL;
        self->closed = 1;
        if (_rc != 0) {{
            PyErr_Format(PyExc_RuntimeError,
                "{close_fn} failed (rc=%d)", (int)_rc);
            return NULL;
        }}"""
    else:
        close_body = f"""        {destroy}
        self->closed = 1;"""

    close = f"""static PyObject *
{tname}_close({obj} *self, PyObject *Py_UNUSED(ignored))
{{
    if (!self->closed && self->h) {{
{close_body}
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

    # ── serializable: state-blob triplet over the backing handle (gh-403) ─────
    # Mirrors the object binding (_context/_methods.py), but over self->h with
    # the closed guard and the module's `backing` C prefix.  The backing core
    # must provide the hand-written triplet (size_t <b>_state_bytes(const T*);
    # void <b>_get_state(const T*, void*); int <b>_set_state(T*, const void*)).
    state_methods = ""
    if C.is_serializable(cfg, module):
        backing = C.handle_backing(cfg, module)
        guard = (
            f"    if (self->closed) {{\n"
            f"        PyErr_SetString(PyExc_RuntimeError,"
            f' "{tname} is closed");\n'
            f"        return NULL;\n"
            f"    }}\n"
        )
        state_methods = f"""static PyObject *
{tname}_state_bytes({obj} *self, PyObject *Py_UNUSED(ignored))
{{
{guard}    return PyLong_FromSize_t({backing}_state_bytes(self->h));
}}

static PyObject *
{tname}_get_state({obj} *self, PyObject *Py_UNUSED(ignored))
{{
{guard}    size_t _n = {backing}_state_bytes(self->h);
    PyObject *_b = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)_n);
    if (!_b)
        return NULL;
    {backing}_get_state(self->h, PyBytes_AS_STRING(_b));
    return _b;
}}

static PyObject *
{tname}_set_state({obj} *self, PyObject *arg)
{{
{guard}    if (!PyBytes_Check(arg)) {{
        PyErr_SetString(PyExc_TypeError, "set_state expects bytes");
        return NULL;
    }}
    if ((size_t)PyBytes_GET_SIZE(arg) != {backing}_state_bytes(self->h)) {{
        PyErr_SetString(PyExc_ValueError, "state blob size mismatch");
        return NULL;
    }}
    if ({backing}_set_state(self->h, PyBytes_AS_STRING(arg)) != 0) {{
        PyErr_SetString(PyExc_ValueError, "set_state rejected the blob");
        return NULL;
    }}
    Py_RETURN_NONE;
}}
"""
        method_rows.append(
            f'    {{"state_bytes", (PyCFunction){tname}_state_bytes,'
            ' METH_NOARGS, "Serialized state size in bytes."},'
        )
        method_rows.append(
            f'    {{"get_state", (PyCFunction){tname}_get_state, METH_NOARGS,'
            ' "Serialize the handle\'s mutable state to bytes."},'
        )
        method_rows.append(
            f'    {{"set_state", (PyCFunction){tname}_set_state, METH_O,'
            ' "Restore mutable state from a get_state() blob."},'
        )
    method_table = "\n".join(method_rows)

    return f"""{struct}
{render_tp_init(cfg, module)}
{methods}
{getsets}
{close}
{ctx}{dealloc}
{state_methods}static PyMethodDef {tname}_methods[] = {{
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
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "{header}"
{weak_decl}""",
        render_enum_tables(cfg, module),
        render_type(cfg, module),
    ]

    # Module-level factories (alternate constructors, gh-565): their functions
    # plus a module PyMethodDef table wired into the moduledef. Absent factories
    # keep the historical NULL m_methods slot (zero churn).
    factories = C.handle_factories(cfg, module)
    if factories:
        parts.append(render_factories(cfg, module))
        _fn_entries = "".join(
            f'    {{"{f["name"]}", (PyCFunction){leaf}_{f["name"]},'
            f" METH_VARARGS,\n"
            f'     "Construct a {tname} via {f["create_fn"]}."}},\n'
            for f in factories
        )
        parts.append(
            f"static PyMethodDef {leaf}_functions[] = {{\n"
            f"{_fn_entries}"
            f"    {{NULL, NULL, 0, NULL}}\n"
            f"}};\n"
        )
        _m_methods = f"{leaf}_functions"
    else:
        _m_methods = "NULL"

    parts.append(f"""static struct PyModuleDef _moduledef = {{
    PyModuleDef_HEAD_INIT, "{leaf}", NULL, -1, {_m_methods},
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


def _pyi_arg_ann(a: dict) -> str:
    """Python annotation for a handle create_arg (path, string, enum, scalar)."""
    if a.get("type") in ("path", "string"):
        return "str"
    if a.get("type") == "bytes":
        return "bytes"  # gh-565: opaque blob init-param
    if a.get("enum"):
        return "str"
    return _pyi_scalar(a.get("type", "int"))


def _method_block(
    doc_blocks: dict[str, str], fn: str | None, name: str | None
) -> DoxyBlock | None:
    """Parse the Doxygen block for C function *fn* out of *doc_blocks*.

    *doc_blocks* maps a C function name to its raw ``/** ... */`` comment (as
    produced by :func:`_docstring.extract_doc_blocks` over the vendored backing
    header). Returns a :class:`_docstring.DoxyBlock`, or ``None`` when there is
    no block or the block is only jm's trivial scaffold brief. *name* lets the
    parser suppress that trivial-brief case (gh-374)."""
    raw = doc_blocks.get(fn) if fn else None
    if not raw:
        return None
    from ._docstring import parse_doxygen_block

    return parse_doxygen_block(raw, name)


def _pyi_class_docstring(
    tname: str,
    create_args: list[dict[str, object]],
    enum_reg: dict[str, list[str]],
    create_block: DoxyBlock | None = None,
) -> list[str]:
    """Indented lines (4-space) for the class-level numpy-style docstring.

    Emits a ``Parameters`` block from *create_args* with default values and
    enum choices surfaced — the content hidden by ``= ...`` in the signature.
    When *create_block* (the parsed Doxygen for the ``create_fn``) is present,
    its ``@brief`` becomes the class summary, its body paragraphs the extended
    description, and its ``@param`` descriptions annotate the matching
    constructor parameters — so the vendored backing header documents the class
    (gh-374). Absent a block, the summary falls back to ``"<Type> handle."`` and
    the output is byte-identical to the pre-enrichment stub."""
    from ._docstring import _wrap, group_paragraphs

    summary = f"{tname} handle."
    body: list[str] = []
    if create_block is not None and create_block.brief:
        summary = create_block.brief
        body = group_paragraphs(create_block.body)
    if not create_args and not body:
        return [f'    """{summary}"""']
    lines: list[str] = [f'    """{summary}', ""]
    for para in body:  # extended description before the Parameters block
        lines += [f"    {w}" for w in _wrap(para, 72)] + [""]
    if not create_args:
        lines.append('    """')
        return lines
    lines += ["    Parameters", "    ----------"]
    for a in create_args:
        n = a["name"]
        ann = _pyi_arg_ann(a)
        type_line = f"    {n} : {ann}"
        if "default" in a:
            dv = a["default"]
            # Numeric defaults bare; string / enum defaults quoted.
            if ann in ("float", "int", "bool"):
                type_line += f", default {dv}"
            else:
                type_line += f', default ``"{dv}"``'
        lines.append(type_line)
        pdesc = create_block.param_desc(n) if create_block else None
        if pdesc:
            lines.append(f"        {pdesc}")
        if a.get("enum"):
            choices = enum_reg.get(a["enum"], [])
            if choices:
                choice_str = ", ".join(f'``"{c}"``' for c in choices)
                lines.append(f"        One of {choice_str}.")
    lines.append('    """')
    return lines


def _pyi_prop_doc(
    fname: str,
    ann: str,
    enum_name: str | None,
    enum_reg: dict[str, list[str]],
) -> str:
    """One-line property docstring text (no surrounding quotes).

    Includes enum choices when *enum_name* is set — the only content
    derivable without header parsing."""
    if enum_name:
        choices = enum_reg.get(enum_name, [])
        if choices:
            choice_str = ", ".join(f'``"{c}"``' for c in choices)
            return f"{fname} ({ann}); one of {choice_str}."
    return f"{fname} ({ann})."


def render_pyi(
    cfg: dict, module: str, doc_blocks: dict[str, str] | None = None
) -> str:
    """Render a class-shaped ``<leaf>.pyi`` for a handle module.

    Emits the class, its ``__init__`` signature (from ``create_args``), each
    method, each decoded-getter property, and (when enabled) ``__enter__`` /
    ``__exit__`` / ``close`` — with numpy-style docstrings. Defaults and enum
    choices are surfaced in the class ``Parameters`` block from the manifest
    (gh-306/gh-374).

    *doc_blocks* maps a C function name to the raw Doxygen block extracted from
    the vendored backing header (see :func:`_docstring.extract_doc_blocks`). When
    supplied, header prose flows into the stub: the ``create_fn``'s ``@brief``
    becomes the class summary, a method ``fn``'s ``@brief``/``@param``/``@return``
    (plus any ``@code`` block, which becomes a runnable ``Examples`` doctest)
    documents that method, and a single-field getter's ``@brief`` documents its
    property. An empty/absent map reproduces the pre-enrichment stub exactly
    (gh-374)."""
    doc_blocks = doc_blocks or {}
    tname = C.handle_type_name(cfg, module)
    C.handle_backing(cfg, module)
    mp = C.module_paths(module)
    enum_reg = C.enums(cfg)
    create_args = C.handle_create_args(cfg, module)

    lines = [
        f"# {mp.leaf}.pyi — type stubs for the {module} handle extension.",
        "#",
        f"# Generated by just-makeit (gh-306). `{tname}` is a typed CPython",
        f"# class over an opaque {C.handle_type(cfg, module)} resource handle.",
        "from __future__ import annotations",
        "",
        "from typing import Any, final",
        "",
        "import numpy as np",
        "from numpy.typing import NDArray",
        "",
        # A handle type is Py_TPFLAGS_DEFAULT (never BASETYPE), so it cannot be
        # subclassed at runtime; @final tells the type checker so — and clears
        # stubtest's "cannot be subclassed / is a disjoint base" mismatches.
        "@final",
        f"class {tname}:",
    ]

    # Class-level docstring: summary + body from the create_fn's Doxygen (when
    # the backing header documents it), then a Parameters block (defaults + enum
    # choices + any header @param prose).
    create_block = _method_block(
        doc_blocks, C.handle_create_fn(cfg, module), None
    )
    lines.extend(
        _pyi_class_docstring(tname, create_args, enum_reg, create_block)
    )

    # __init__ from create_args (docstring lives on the class above).
    init_params = []
    for a in create_args:
        ann = _pyi_arg_ann(a)
        if "default" in a or a.get("kwonly"):
            init_params.append(f"{a['name']}: {ann} = ...")
        else:
            init_params.append(f"{a['name']}: {ann}")
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
        if returns == "bytes":
            # (f) scalar/string args -> bytes (len from handle, gh-565).
            sig = "self" + "".join(
                f", {a['name']}: "
                + (
                    "str"
                    if a.get("type") == "string"
                    else _pyi_scalar(a["type"])
                )
                for a in margs
            )
            ann = "bytes"
            doc_call = f"{name}({', '.join(a['name'] for a in margs)})"
        elif writable_out and ret_arr:
            # (d) execute(x, out) -> ndarray
            sig = "self, x: NDArray[Any], out: NDArray[Any]"
            ann = "NDArray[Any]"
            doc_call = f"{name}(x, out)"
        elif ret_arr and not arrays and m.get("out_len_fn"):
            # (e) render(overrides_json) / at(snr, seed) -> ndarray (len from handle)
            sig = "self, " + ", ".join(
                f"{a['name']}: "
                + (
                    "str"
                    if a.get("type") == "string"
                    else _pyi_scalar(a["type"])
                )
                for a in margs
            )
            ann = "NDArray[Any]"
            doc_call = f"{name}({', '.join(a['name'] for a in margs)})"
        elif ret_arr and not arrays:
            # (c) read(n) -> ndarray
            sig = "self, n: int"
            ann = "NDArray[Any]"
            doc_call = f"{name}(n)"
        elif arrays:
            # (b) x[, scalars] -> scalar / None; a trailing `default` shows as
            # `= ...` (gh-178 review #6).
            parts = ["self", "x: NDArray[Any]"] + [
                f"{s['name']}: {_pyi_scalar(s['type'])}"
                + (" = ..." if s.get("default") is not None else "")
                for s in scalars
            ]
            sig = ", ".join(parts)
            ann = _pyi_scalar(returns) if returns else "None"
            # Inline actual scalar defaults in the docstring summary.
            scalar_doc = ["x"] + [
                f"{s['name']}={s['default']}"
                if s.get("default") is not None
                else s["name"]
                for s in scalars
            ]
            doc_call = f"{name}({', '.join(scalar_doc)})"
        elif margs:
            # (a) scalar / path args -> scalar / None; a `default` shows as
            # `= ...` (#319). A path arg is `str` (gh-565); an `error` status
            # method returns None (the int rc is consumed as the raise trigger).
            sig = "self, " + ", ".join(
                f"{a['name']}: {_pyi_arg_ann(a)}"
                + (" = ..." if a.get("default") is not None else "")
                for a in margs
            )
            ann = (
                "None"
                if m.get("error")
                else (_pyi_scalar(returns) if returns else "None")
            )
            arg_doc = [
                f"{a['name']}={a['default']}"
                if a.get("default") is not None
                else a["name"]
                for a in margs
            ]
            doc_call = f"{name}({', '.join(arg_doc)})"
        else:
            sig = "self"
            ann = _pyi_scalar(returns) if returns else "None"
            doc_call = f"{name}()"
        lines.append(f"    def {name}({sig}) -> {ann}:")
        # Header prose (from the method's C `fn` Doxygen) upgrades the one-line
        # stub to a full numpy docstring — @param/@return prose plus a runnable
        # @code doctest. Python-facing args only: an array arg is NDArray, the
        # rest map through _pyi_arg_ann (gh-374).
        m_block = _method_block(doc_blocks, m.get("fn"), name)
        if m_block is not None:
            from ._stubs import _numpy_doc_lines

            py_params = [
                (
                    a["name"],
                    "NDArray[Any]"
                    if str(a.get("type", "")).endswith("[]")
                    else _pyi_arg_ann(a),
                )
                for a in margs
            ]
            lines.extend(
                _numpy_doc_lines(m_block, name, py_params, ann, indent=8)
            )
        else:
            lines.append(f'        """{doc_call} -> {ann}."""')

    # decoded-getter properties (a writable_fn field also gets a setter).
    for g in C.handle_getters(cfg, module):
        # A single-field getter's @brief documents its one property; a
        # multi-field struct getter carries one @brief for the whole struct,
        # which cannot name each field, so those stay manifest-synthesized
        # (gh-374 — the same header-vs-manifest split views_module documents).
        g_fields = g.get("fields", [])
        g_block = (
            _method_block(doc_blocks, g.get("fn"), None)
            if len(g_fields) == 1
            else None
        )
        for f in g_fields:
            enum_name: str | None = f.get("enum")
            ann = "str" if enum_name else _pyi_scalar(f["type"])
            if g_block is not None and g_block.brief and not enum_name:
                doc = g_block.brief
            else:
                doc = _pyi_prop_doc(f["name"], ann, enum_name, enum_reg)
            lines.append("    @property")
            lines.append(f"    def {f['name']}(self) -> {ann}:")
            lines.append(f'        """{doc}"""')
            if f.get("writable_fn"):
                lines.append(f"    @{f['name']}.setter")
                lines.append(
                    f"    def {f['name']}(self, value: {ann}) -> None: ..."
                )

    # serializable state triplet (gh-403).
    if C.is_serializable(cfg, module):
        lines.append("    def state_bytes(self) -> int:")
        lines.append('        """Serialized state size in bytes."""')
        lines.append("    def get_state(self) -> bytes:")
        lines.append(
            '        """Serialize the handle\'s mutable state to bytes."""'
        )
        lines.append("    def set_state(self, blob: bytes) -> None:")
        lines.append(
            '        """Restore mutable state from a get_state() blob."""'
        )

    # RAII surface.
    lines.append("    def close(self) -> None:")
    lines.append('        """Release the handle and free resources."""')
    if C.handle_context(cfg, module):
        lines.append(f"    def __enter__(self) -> {tname}:")
        lines.append('        """Enter context; return self."""')
        lines.append("    def __exit__(self, *exc: Any) -> None:")
        lines.append('        """Exit context and close the handle."""')
    lines.append("")

    # Module-level factories (alternate constructors, gh-565): free functions
    # that build a fresh handle via an alt create_fn and return the typed class.
    for f in C.handle_factories(cfg, module):
        ips = list(f.get("init_params", []))
        sig = ", ".join(f"{a['name']}: {_pyi_arg_ann(a)}" for a in ips)
        lines.append(f"def {f['name']}({sig}) -> {tname}:")
        lines.append(f'    """Construct a {tname} via {f["create_fn"]}."""')
        lines.append("")

    return "\n".join(lines)


# ── materialization (driven by jm apply's _replay; mirrors _capsule) ──────────


def _backing_doc_blocks(
    cfg: dict, module: str, project_root: Path | None
) -> dict[str, str]:
    """Parse Doxygen from the module's vendored backing header, keyed by fn.

    The vendored resource lives under the *real* project's ``native/inc``, not
    the pristine replay scaffold (a ``c_deps`` header is never re-materialized),
    so this reads from *project_root*. Returns ``{}`` when the header is absent
    or *project_root* is unknown, which reproduces the un-enriched stub exactly
    (gh-374)."""
    header_rel = C.handle_header(cfg, module)
    if not header_rel or project_root is None:
        return {}
    hp = Path(project_root) / "native" / "inc" / header_rel
    if not hp.exists():
        return {}
    from ._docstring import extract_doc_blocks

    return extract_doc_blocks(hp.read_text(encoding="utf-8"))


def materialize(
    cfg: dict, root: Path, module: str, project_root: Path | None = None
) -> None:
    """Write a handle module's generated files into *root* (a project tree).

    Emits the binding ``<cname>_ext.c``, the module ``CMakeLists.txt``, and the
    ``.pyi`` stub, then wires ``add_subdirectory`` into the top ``CMakeLists.txt``
    under the ``# ── Modules`` sentinel. Mirrors ``_capsule.materialize`` — the
    handle shape has no ``_core`` / per-object scaffolding either.

    *project_root* points at the real project so the ``.pyi`` can pick up
    Doxygen from the vendored backing header (gh-374); it defaults to *root*
    for a direct materialize."""
    from ._init import _write

    pkg = C.project_name(cfg)
    mp = C.module_paths(module)
    out_pkg = C.handle_package(cfg, module) or mp.pypath
    doc_blocks = _backing_doc_blocks(cfg, module, project_root or root)

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
        render_pyi(cfg, module, doc_blocks),
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
