"""
_capsule.py — code generator for ``kind = "capsule"`` modules (gh-286).

A capsule module exposes its C state as free functions over an opaque
``PyCapsule`` instead of a ``PyTypeObject``:

    state = <backing>_create(<init params>)      # -> capsule
    y     = <backing>_execute(state, x, out)     # numpy in -> numpy view
            <backing>_reset(state)
            <backing>_destroy(state)
    v     = <backing>_get_<prop>(state)
            <backing>_set_<prop>(state, v)        # writable props only

The generated binding is pure glue — capsule lifetime, the use-after-destroy
guard, numpy marshaling, and the optional GIL release. The kernel bodies stay
hand-written in ``<backing>_core.c``. This mirrors doppler's hand-written
``ddc_fn`` extension exactly, so that module can drop ``no_generate`` and adopt
``kind = "capsule"``.
"""

from __future__ import annotations

from . import _config as C
from . import _types as T

# ── small type helpers ───────────────────────────────────────────────────────


def _scalar_fmt(ptype: str) -> str:
    """PyArg_ParseTuple format char for a scalar C type."""
    return T._CTYPE_META[ptype]["fmt"]


def _to_py(ptype: str, expr: str) -> str:
    """C expression converting a scalar C value to a new PyObject."""
    return T._CTYPE_META[ptype]["to_py"](expr)


def _array_elem_npy(array_type: str) -> tuple[str, str]:
    """Return (C element type, NPY enum) for an ``T[]`` array type."""
    parsed = T.parse_array_type(array_type) or None
    elem = array_type[:-2].strip() if array_type.endswith("[]") else array_type
    if parsed:
        elem = parsed[0]
    npy = T._CTYPE_TO_NPY.get(elem)
    if npy is None:
        raise ValueError(f"capsule: unsupported array element type '{elem}'")
    return elem, npy


# ── per-function emitters ────────────────────────────────────────────────────


def _emit_create(backing: str, init_params: list[tuple]) -> str:
    names = [p[0] for p in init_params]
    fmt = "".join(_scalar_fmt(p[1]) for p in init_params)
    decls = "".join(f"    {p[1]} {p[0]};\n" for p in init_params)
    addrs = ", ".join(f"&{n}" for n in names)
    call_args = ", ".join(names)
    parse = (
        f'    if (!PyArg_ParseTuple(args, "{fmt}", {addrs}))\n'
        "        return NULL;\n"
        if init_params
        else "    (void)args;\n"
    )
    return f"""static PyObject *
_fn_{backing}_create(PyObject *mod, PyObject *args)
{{
    (void)mod;
{decls}{parse}
    _wrap_t *w = (_wrap_t *)malloc(sizeof(_wrap_t));
    if (!w) return PyErr_NoMemory();

    w->state = {backing}_create({call_args});
    if (!w->state) {{ free(w); return PyErr_NoMemory(); }}
    w->destroyed = 0;

    PyObject *cap = PyCapsule_New(w, _CAPS, _wrap_destructor);
    if (!cap) {{ {backing}_destroy(w->state); free(w); return NULL; }}
    return cap;
}}
"""


def _emit_execute(backing: str, method: dict) -> str:
    """Emit a ``variable_output`` execute: numpy-in -> caller-owned numpy view.

    Signature mirrors jm's variable-output-with-capacity form::

        size_t <backing>_<name>(state, const IN *in, size_t n_in,
                                OUT *out, size_t max_out);
    """
    name = method["name"]
    in_elem, in_npy = _array_elem_npy(method["arg_type"])
    out_elem, out_npy = _array_elem_npy(method["return_type"])
    nogil = bool(method.get("nogil"))
    gil_open = "    Py_BEGIN_ALLOW_THREADS\n" if nogil else ""
    gil_close = "    Py_END_ALLOW_THREADS\n" if nogil else ""
    return f"""static PyObject *
_fn_{backing}_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap, *x_obj, *out_obj;
    if (!PyArg_ParseTuple(args, "OOO", &cap, &x_obj, &out_obj))
        return NULL;

    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;

    PyArrayObject *x_arr = (PyArrayObject *)PyArray_FROM_OTF(
        x_obj, {in_npy}, NPY_ARRAY_C_CONTIGUOUS);
    if (!x_arr) return NULL;

    /* Require the exact output dtype — no silent cast; a cast would write
     * into a temp copy instead of the caller's buffer. */
    if (!PyArray_Check(out_obj) ||
        PyArray_TYPE((PyArrayObject *)out_obj) != {out_npy} ||
        !PyArray_ISWRITEABLE((PyArrayObject *)out_obj)) {{
        PyErr_SetString(PyExc_TypeError,
            "out must be a writable ndarray of the output dtype");
        Py_DECREF(x_arr);
        return NULL;
    }}
    PyArrayObject *out_arr = (PyArrayObject *)PyArray_FROM_OTF(
        out_obj, {out_npy}, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE);
    if (!out_arr) {{ Py_DECREF(x_arr); return NULL; }}

    size_t n_in    = (size_t)PyArray_SIZE(x_arr);
    size_t max_out = (size_t)PyArray_SIZE(out_arr);

    const {in_elem} *in_data  = (const {in_elem} *)PyArray_DATA(x_arr);
    {out_elem} *out_data = ({out_elem} *)PyArray_DATA(out_arr);
    size_t n_out;
{gil_open}    n_out = {backing}_{name}(w->state, in_data, n_in, out_data, max_out);
{gil_close}    Py_DECREF(x_arr);

    /* Return out_arr[:n_out] — zero-copy view into the caller's buffer. */
    PyObject *stop  = PyLong_FromSsize_t((Py_ssize_t)n_out);
    PyObject *slice = stop ? PySlice_New(NULL, stop, NULL) : NULL;
    Py_XDECREF(stop);
    PyObject *view  = slice ? PyObject_GetItem((PyObject *)out_arr, slice)
                            : NULL;
    Py_XDECREF(slice);
    Py_DECREF(out_arr);
    return view;
}}
"""


def _emit_void_method(backing: str, name: str) -> str:
    """A bare ``(state) -> None`` method (e.g. reset)."""
    return f"""static PyObject *
_fn_{backing}_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    {backing}_{name}(w->state);
    Py_RETURN_NONE;
}}
"""


def _emit_destroy(backing: str) -> str:
    return f"""static PyObject *
_fn_{backing}_destroy(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    {backing}_destroy(w->state);
    w->state     = NULL;
    w->destroyed = 1;
    Py_RETURN_NONE;
}}
"""


def _emit_getset(backing: str, prop: dict) -> str:
    name, ptype = prop["name"], prop["type"]
    out = f"""static PyObject *
_fn_{backing}_get_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    return {_to_py(ptype, f"{backing}_get_{name}(w->state)")};
}}
"""
    if prop.get("writable"):
        fmt = _scalar_fmt(ptype)
        out += f"""
static PyObject *
_fn_{backing}_set_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    {ptype} {name};
    if (!PyArg_ParseTuple(args, "O{fmt}", &cap, &{name})) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    {backing}_set_{name}(w->state, {name});
    Py_RETURN_NONE;
}}
"""
    return out


# ── whole-file assembly ──────────────────────────────────────────────────────


def _fn_list(cfg: dict, module: str) -> list[str]:
    """Ordered free-function names the module exposes (for the method table)."""
    backing = C.capsule_backing(cfg, module)
    names = [f"{backing}_create"]
    names += [f"{backing}_{m['name']}" for m in C.module_methods(cfg, module)]
    names.append(f"{backing}_destroy")
    for p in C.module_properties(cfg, module):
        names.append(f"{backing}_get_{p['name']}")
        if p.get("writable"):
            names.append(f"{backing}_set_{p['name']}")
    return names


def render_ext(cfg: dict, module: str) -> str:
    """Render the full ``<module>_ext.c`` for a capsule module."""
    backing = C.capsule_backing(cfg, module)
    caps = C.capsule_name(cfg, module) or (
        f"{C.project_name(cfg)}.{module}.{backing}_state"
    )
    # The backing C API header; defaults to <backing>/<backing>_core.h but a
    # composed object may declare its API elsewhere (ddcr lives in ddc_core.h).
    header = (
        cfg.get("module", {}).get(module, {}).get("header")
        or f"{backing}/{backing}_core.h"
    )
    init_params = C.module_init_params(cfg, module)

    parts: list[str] = []
    parts.append(f"""/*
 * {module}_ext.c — capsule extension for `{backing}` (generated by jm; gh-286).
 *
 * State is an opaque PyCapsule; the kernel bodies live in {backing}_core.c.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>
#include <stdlib.h>

#include "{header}"

static const char _CAPS[] = "{caps}";

typedef struct {{
    {backing}_state_t *state;
    int                destroyed;
}} _wrap_t;

static void
_wrap_destructor(PyObject *cap)
{{
    _wrap_t *w = (_wrap_t *)PyCapsule_GetPointer(cap, _CAPS);
    if (!w) return;
    if (!w->destroyed)
        {backing}_destroy(w->state);
    free(w);
}}

static _wrap_t *
_get_wrap(PyObject *cap)
{{
    _wrap_t *w = (_wrap_t *)PyCapsule_GetPointer(cap, _CAPS);
    if (!w) return NULL;
    if (w->destroyed) {{
        PyErr_SetString(PyExc_RuntimeError,
                        "{backing}_state has already been destroyed");
        return NULL;
    }}
    return w;
}}
""")

    parts.append(_emit_create(backing, init_params))
    for m in C.module_methods(cfg, module):
        if m.get("caller_out") or m.get("arg_type"):
            parts.append(_emit_execute(backing, m))
        else:
            parts.append(_emit_void_method(backing, m["name"]))
    parts.append(_emit_destroy(backing))
    for p in C.module_properties(cfg, module):
        parts.append(_emit_getset(backing, p))

    # ── method table ──
    fn_names = _fn_list(cfg, module)
    rows = []
    for fn in fn_names:
        sig = _fn_signature(cfg, module, fn)
        rows.append(f'    {{"{fn}", _fn_{fn}, METH_VARARGS,\n     "{sig}"}},')
    method_table = "\n".join(rows)

    parts.append(f"""static PyMethodDef _methods[] = {{
{method_table}
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef _moduledef = {{
    PyModuleDef_HEAD_INIT, "{module}", NULL, -1, _methods,
    NULL, NULL, NULL, NULL
}};

PyMODINIT_FUNC
PyInit_{module}(void)
{{
    import_array();
    return PyModule_Create(&_moduledef);
}}
""")
    return "\n".join(parts)


def _fn_signature(cfg: dict, module: str, fn: str) -> str:
    """A one-line ``name(args) -> ret`` docstring for the method table.

    Rich numpy docstrings are a follow-up (header-derived, like jm's other
    docstring synthesis); this keeps the generated table self-describing."""
    backing = C.capsule_backing(cfg, module)
    if fn == f"{backing}_create":
        params = ", ".join(p[0] for p in C.module_init_params(cfg, module))
        return f"{fn}({params}) -> state"
    if fn == f"{backing}_destroy":
        return f"{fn}(state) -> None"
    for m in C.module_methods(cfg, module):
        if fn == f"{backing}_{m['name']}":
            if m.get("arg_type"):
                return f"{fn}(state, x, out) -> ndarray"
            return f"{fn}(state) -> None"
    for p in C.module_properties(cfg, module):
        if fn == f"{backing}_get_{p['name']}":
            return f"{fn}(state) -> {p['type']}"
        if fn == f"{backing}_set_{p['name']}":
            return f"{fn}(state, {p['name']}) -> None"
    return f"{fn}(...)"
