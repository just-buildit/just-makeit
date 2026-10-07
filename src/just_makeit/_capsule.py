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

from . import _textio

from pathlib import Path

from . import _coerce
from . import _config as C
from . import _csym as CSYM
from ._builtins import require_scope_names
from . import _modplatforms
from . import _render as R
from . import _procglobal
from . import _types as T
from . import _incpath as INC
from ._context._modpath import module_docstring_lines, module_m_doc
from ._context._parse import _build_ml_doc
from ._report import Refusal

# ── small type helpers ───────────────────────────────────────────────────────


#: gh-2009: what a constructor row that parses scalars only says after it
#: refuses an array -- where a constructor array IS supported.
CTOR_ARRAY_HOME = (
    "a constructor array is supported on an object's `[[<obj>.init_params]]`"
)


def scalar_meta(ptype: str, where: str, home: str = "") -> dict:
    """The ``_CTYPE_META`` row of one scalar a kind module converts.

    The one lookup behind :func:`_scalar_fmt` and :func:`_to_py`, which every
    capsule and handle row that crosses as a single C scalar reaches: a
    capsule's ``init_params`` and ``properties``; a handle's ``create_args``,
    factory ``init_params``, method scalar ``args`` and ``returns``, and
    getter ``fields``.

    gh-2009: it indexed ``_CTYPE_META`` directly, so a type outside it -- an
    array such as ``"float[]"``, or a spelling jm does not know -- left
    ``jm apply`` as a bare ``KeyError: 'float[]'`` from inside the renderer,
    naming neither the module nor the row. It is refused here instead, as
    one ``error:`` line naming both. ``apply`` renders into a throwaway tree
    first, so the refusal leaves the project byte-identical.

    Refused at the lookup, not by a check in front of the emitters: each row
    reaches this only after its own other shapes (``path``, ``string``,
    ``bytes``, ``enum``, a method's array argument) have branched away, so a
    check beside it would have to restate every one of those branches, and
    would refuse a row that works the day it fell behind one.

    Parameters
    ----------
    ptype : str
        The row's declared ``type``.
    where : str
        The row, as the refusal names it, e.g.
        ``capsule module 'cap' init_params row 'h'``.
    home : str, optional
        Said after an array is refused: where one is supported instead.
        :data:`CTOR_ARRAY_HOME` on a constructor row.

    Returns
    -------
    dict
        ``_CTYPE_META[ptype]``.

    Raises
    ------
    Refusal
        When *ptype* is not a scalar jm converts.

    Examples
    --------
    >>> scalar_meta("double", "x")["fmt"]
    'd'
    >>> try:
    ...     scalar_meta("float[]", "capsule module 'cap' init_params row 'h'",
    ...                 CTOR_ARRAY_HOME)
    ... except Refusal as e:
    ...     print(e)
    capsule module 'cap' init_params row 'h': `type = "float[]"` is an array, and this row crosses as one C scalar; a constructor array is supported on an object's `[[<obj>.init_params]]`.
    """
    meta = T._CTYPE_META.get(ptype)
    if meta is not None:
        return meta
    if str(ptype).rstrip().endswith("]"):
        why = (
            f'`type = "{ptype}"` is an array, and this row crosses as one C '
            f"scalar"
        )
        if home:
            why += f"; {home}"
    else:
        why = (
            f"unknown type '{ptype}'; jm converts this value as one C "
            f"scalar, one of {', '.join(sorted(T._CTYPE_META))}"
        )
    raise Refusal(f"{where}: {why}.")


def _scalar_fmt(ptype: str, where: str, home: str = "") -> str:
    """PyArg_ParseTuple format char for a scalar C type; see `scalar_meta`."""
    return scalar_meta(ptype, where, home)["fmt"]


def _to_py(ptype: str, expr: str, where: str) -> str:
    """C expression converting a scalar C value to a new PyObject.

    Refuses a type that is not a scalar jm converts, naming *where*; see
    :func:`scalar_meta`.
    """
    return scalar_meta(ptype, where)["to_py"](expr)


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

#: gh-1525: what :func:`_emit_create` declares beside the init params, which
#: become its C locals verbatim. Its signature is ``(mod, args)``, not a
#: method's ``(self, args, kwds)``, and it holds the wrapper in ``w`` and the
#: capsule in ``cap`` -- an init param named any of those redeclared it.
CREATE_LOCALS = frozenset({"mod", "w", "cap"})


def arg_scopes(
    cfg: dict, module: str
) -> "list[tuple[str, str, list, frozenset[str]]]":
    """Every generated wrapper whose C locals a manifest name becomes.

    ``(owner, C function, params, declares)`` -- the arguments
    :func:`~just_makeit._builtins.require_param_names` takes, plus the
    wrapper they describe, which is how
    ``tests/test_gh1525_kind_arg_local_names.py`` holds *declares* to the
    rendered C. The execute / reset / property wrappers name nothing after
    the manifest, so only ``create`` is here.
    """
    backing = C.capsule_backing(cfg, module)
    return [
        (
            f"capsule module '{module}' init_params",
            f"_fn_{backing}_create",
            C.module_init_params(cfg, module),
            CREATE_LOCALS,
        )
    ]


def _emit_create(
    backing: str, sym: str, init_params: list[tuple], module: str
) -> str:
    names = [p[0] for p in init_params]
    fmt = "".join(
        _scalar_fmt(
            p[1],
            f"capsule module '{module}' init_params row '{p[0]}'",
            CTOR_ARRAY_HOME,
        )
        for p in init_params
    )
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

    w->state = {sym}_create({call_args});
    if (!w->state) {{ free(w); return PyErr_NoMemory(); }}
    w->destroyed = 0;

    PyObject *cap = PyCapsule_New(w, _CAPS, _wrap_destructor);
    if (!cap) {{ {sym}_destroy(w->state); free(w); return NULL; }}
    return cap;
}}
"""


def _emit_execute(backing: str, sym: str, method: dict) -> str:
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
    _out_guard = _coerce.out_buffer_guard(
        "out_obj", out_npy, decrefs="Py_DECREF(x_arr);"
    )
    # gh-1716: the slice below clamps, so nothing is READ past `out` -- but
    # a count past `max_out` means the kernel wrote past it, and a quietly
    # trimmed view would hand back data from a call that overran.
    _n_out_guard = _coerce.returned_count_c(
        "n_out", "max_out", f"{backing}_{name}", "Py_DECREF(out_arr);"
    )
    return f"""static PyObject *
_fn_{backing}_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap, *x_obj, *out_obj;
    if (!PyArg_ParseTuple(args, "OOO", &cap, &x_obj, &out_obj))
        return NULL;

    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;

    PyArrayObject *x_arr =
        {_coerce.array_arg("x_obj", in_npy, "NPY_ARRAY_C_CONTIGUOUS", "x")};
    if (!x_arr) return NULL;

{_out_guard}    PyArrayObject *out_arr =
        {_coerce.array_arg("out_obj", out_npy, "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE", "out")};
    if (!out_arr) {{ Py_DECREF(x_arr); return NULL; }}

    size_t n_in    = (size_t)PyArray_SIZE(x_arr);
    size_t max_out = (size_t)PyArray_SIZE(out_arr);

    const {in_elem} *in_data  = (const {in_elem} *)PyArray_DATA(x_arr);
    {out_elem} *out_data = ({out_elem} *)PyArray_DATA(out_arr);
    size_t n_out;
{gil_open}    n_out = {sym}_{name}(w->state, in_data, n_in, out_data, max_out);
{gil_close}    Py_DECREF(x_arr);
{_n_out_guard}
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


def _emit_void_method(backing: str, sym: str, name: str) -> str:
    """A bare ``(state) -> None`` method (e.g. reset)."""
    return f"""static PyObject *
_fn_{backing}_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    {sym}_{name}(w->state);
    Py_RETURN_NONE;
}}
"""


def _emit_destroy(backing: str, sym: str) -> str:
    return f"""static PyObject *
_fn_{backing}_destroy(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    {sym}_destroy(w->state);
    w->state     = NULL;
    w->destroyed = 1;
    Py_RETURN_NONE;
}}
"""


def _emit_getset(backing: str, sym: str, prop: dict, module: str) -> str:
    name, ptype = prop["name"], prop["type"]
    where = f"capsule module '{module}' properties row '{name}'"
    value = _to_py(
        ptype, f"{CSYM.property_getter(sym, name)}(w->state)", where
    )
    out = f"""static PyObject *
_fn_{backing}_get_{name}(PyObject *mod, PyObject *args)
{{
    (void)mod;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) return NULL;
    _wrap_t *w = _get_wrap(cap);
    if (!w) return NULL;
    return {value};
}}
"""
    if prop.get("writable"):
        fmt = _scalar_fmt(ptype, where)
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
    {sym}_set_{name}(w->state, {name});
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
    # gh-1685: the C symbols the binding calls, from the backing's C stem;
    # everything else below (header, capsule name, Python names) keeps the
    # FILE stem `backing`.
    sym = CSYM.backing_stem(cfg, backing)
    caps = C.capsule_name(cfg, module) or (
        f"{C.project_name(cfg)}.{module}.{backing}_state"
    )
    # The backing C API header; defaults to <backing>/<backing>_core.h but a
    # composed object may declare its API elsewhere (ddcr lives in ddc_core.h).
    header = cfg.get("module", {}).get(module, {}).get(
        "header"
    ) or INC.core_include(backing, cfg)
    init_params = C.module_init_params(cfg, module)

    parts: list[str] = []
    # gh-1583: the include of a jm header is spelled by the layout.
    _common_h = INC.include("clib_common.h", cfg)
    parts.append(f"""/*
 * {module}_ext.c — capsule extension for `{backing}` (generated by jm; gh-286).
 *
 * State is an opaque PyCapsule; the kernel bodies live in {backing}_core.c.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include "{_common_h}"
#include <stdlib.h>

#include "{header}"

{_coerce.ARRAY_ARG_C}
static const char _CAPS[] = "{caps}";

typedef struct {{
    {sym}_state_t *state;
    int                destroyed;
}} _wrap_t;

static void
_wrap_destructor(PyObject *cap)
{{
    _wrap_t *w = (_wrap_t *)PyCapsule_GetPointer(cap, _CAPS);
    if (!w) return;
    if (!w->destroyed)
        {sym}_destroy(w->state);
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

    parts.append(_emit_create(backing, sym, init_params, module))
    for m in C.module_methods(cfg, module):
        if m.get("caller_out") or m.get("arg_type"):
            parts.append(_emit_execute(backing, sym, m))
        else:
            parts.append(_emit_void_method(backing, sym, m["name"]))
    parts.append(_emit_destroy(backing, sym))
    for p in C.module_properties(cfg, module):
        parts.append(_emit_getset(backing, sym, p, module))

    # ── method table ──
    fn_names = _fn_list(cfg, module)
    rows = []
    for fn in fn_names:
        doc_c = _fn_doc_c(cfg, module, fn)
        rows.append(f'    {{"{fn}", _fn_{fn}, METH_VARARGS,\n     {doc_c}}},')
    method_table = "\n".join(rows)

    parts.append(f"""static PyMethodDef _methods[] = {{
{method_table}
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef _moduledef = {{
    PyModuleDef_HEAD_INIT, "{module}", {module_m_doc(cfg, module)}, -1, _methods,
    NULL, NULL, NULL, NULL
}};

PyMODINIT_FUNC
PyInit_{module}(void)
{{
    import_array();
{_capsule_init_body(cfg, module)}}}
""")
    return "\n".join(parts)


def _capsule_init_body(cfg: dict, module: str) -> str:
    """The tail of a capsule module's ``PyInit_``.

    A capsule module has no types to register, so its init has always been a
    one-liner and needs no module variable. gh-1117's rendezvous does need
    one — so the longer form appears ONLY when there is a rendezvous to emit,
    and every capsule module declaring no process-global core renders
    byte-identically to before. `jm status --check` compares generated files
    byte-for-byte, so the alternative would report drift in every existing
    capsule project in exchange for nothing.
    """
    rz = _procglobal.rendezvous_c(cfg, module)
    if not rz:
        return "    return PyModule_Create(&_moduledef);\n"
    return (
        "    PyObject *m = PyModule_Create(&_moduledef);\n"
        "    if (!m) return NULL;\n" + rz + "    return m;\n"
    )


def _fn_doc_lines(cfg: dict, module: str, fn: str) -> list[str]:
    """Free function *fn*'s authored docstring, as the ``.pyi`` carries it.

    ``[]`` when the manifest documents nothing about it -- the stub then
    keeps its ``...`` body and the method table its `_fn_signature` line, so
    a capsule module with no ``doc`` renders byte-identically.

    gh-1499: every ``doc`` a capsule module accepts was read by nothing. A
    method's documents ``<backing>_<name>``; a property's documents both
    ``<backing>_get_<name>`` and the setter, which are the one property
    split in two; an init param's needs a ``Parameters`` section on
    ``<backing>_create``, so it takes the numpy render with the signature
    line as its summary. The runtime ``ml_doc`` is derived from these lines,
    so the two faces are one text.
    """
    from ._docstring import (
        authored_doc_lines,
        authored_docstring,
        render_numpy_doc,
    )

    backing = C.capsule_backing(cfg, module)
    mod = cfg.get("module", {}).get(module, {})
    if fn == f"{backing}_create":
        ips = list(mod.get("init_params", []))
        pdocs = {p["name"]: str(p["doc"]) for p in ips if p.get("doc")}
        if not pdocs:
            return []
        return render_numpy_doc(
            None,
            fn,
            [(p["name"], _pyi_scalar(p["type"])) for p in ips],
            "Any",
            override=_fn_signature(cfg, module, fn),
            param_docs=pdocs,
            indent=4,
        )
    doc = ""
    for m in C.module_methods(cfg, module):
        if fn == f"{backing}_{m['name']}":
            doc = str(m.get("doc") or "")
    for p in C.module_properties(cfg, module):
        if fn in (f"{backing}_get_{p['name']}", f"{backing}_set_{p['name']}"):
            doc = str(p.get("doc") or "")
    lines = authored_doc_lines(doc)
    return authored_docstring(lines, 4) if lines else []


def _fn_doc_c(cfg: dict, module: str, fn: str) -> str:
    """*fn*'s ``ml_doc`` literal: its authored docstring, else the one-line
    signature the table has always carried (gh-1499)."""
    from ._docstring import docstring_body

    lines = _fn_doc_lines(cfg, module, fn)
    if not lines:
        return f'"{_fn_signature(cfg, module, fn)}"'
    return _build_ml_doc(docstring_body(lines, 4))


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


# ── CMakeLists.txt ───────────────────────────────────────────────────────────


def render_cmake(cfg: dict, module: str) -> str:
    """Render the ``native/src/<cname>/CMakeLists.txt`` for a capsule module.

    A capsule module owns no ``_core`` of its own — the backing kernel is one
    of its ``depends_on`` components. So the file is a single Python-extension
    target that links every ``link = true`` dependency's ``<name>_core`` plus
    ``extra_link_libs``, and (like every jm Python module) drops the built
    ``.so`` into the package directory and copies it next to the sources so an
    editable install resolves the import.
    """
    mp = C.module_paths(module)
    leaf, cname = mp.leaf, mp.cname
    out_pkg = C.capsule_package(cfg, module) or mp.pypath

    link_cores = C.dep_link_libs(C.capsule_depends_on(cfg, module))
    extra = C.capsule_extra_link_libs(cfg, module)
    link_lines = "".join(f"    {lib}\n" for lib in link_cores + extra)

    return R.with_extra_cmake(
        f"""if({_modplatforms.cmake_guard(cfg, module)})

# {cname} — capsule extension for `{C.capsule_backing(cfg, module)}` (gh-286).
# State is an opaque PyCapsule; the kernel bodies live in the backing _core.c.
# Generated by just-makeit from [module.{module}] — edit the manifest, not this.
Python3_add_library({leaf} MODULE WITH_SOABI {cname}_ext.c)
target_link_libraries({leaf} PRIVATE
{link_lines}    Python3::NumPy)
target_include_directories({leaf} PRIVATE {INC.CMAKE_INC})
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
""",
        cname,
    )


# ── .pyi type stub ───────────────────────────────────────────────────────────


def _pyi_scalar(ptype: str) -> str:
    """Python annotation for a scalar C init-param / property type."""
    if ptype in ("float", "double", "long double"):
        return "float"
    if ptype == "bool":
        return "bool"
    return "int"


def render_pyi(cfg: dict, module: str) -> str:
    """Render a thin ``<leaf>.pyi`` for a capsule module.

    Signatures only: the create / execute / reset / destroy / get_ / set_ free
    functions over the opaque capsule handle. A function the manifest
    documents carries its ``doc`` as its docstring (gh-1499, `_fn_doc_lines`);
    the rest keep a ``...`` body, so an undocumented module is unchanged.
    """
    backing = C.capsule_backing(cfg, module)
    init_params = C.module_init_params(cfg, module)

    lines = [
        f"# {C.module_paths(module).leaf}.pyi — type stubs for the {module} "
        "capsule extension.",
        "#",
        "# Generated by just-makeit (gh-286). State is held in an opaque",
        "# PyCapsule rather than a Python type; the handle is created by",
        f"# {backing}_create and consumed by the other {backing}_* functions.",
        "# The handle (a `state` argument) is an opaque PyCapsule, typed `Any`.",
        # gh-1499: `[module.X] doc`, the module's docstring; `render_ext`
        # puts the same text in `m_doc`.
        *module_docstring_lines(cfg, module),
        "from typing import Any",
        "",
        "import numpy as np",
        "import numpy.typing as npt",
        "from numpy.typing import NDArray",
        "",
    ]

    def _def(fn: str, head: str) -> None:
        """``head ...`` -- or *head* over the function's authored docstring."""
        doc = _fn_doc_lines(cfg, module, fn)
        lines.extend([head, *doc] if doc else [f"{head} ..."])

    # The opaque capsule handle is `Any` — a named module-level alias (e.g.
    # `GADGETState = Any`) would read to stubtest as a runtime constant the C
    # extension never defines, so the `state` parameters are annotated `Any`
    # inline and the header comment above names what they carry.
    state_t = "Any"
    ctor_args = ", ".join(
        f"{n}: {_pyi_scalar(t)}" for (n, t, _d) in init_params
    )
    _def(
        f"{backing}_create", f"def {backing}_create({ctor_args}) -> {state_t}:"
    )

    for m in C.module_methods(cfg, module):
        name = m["name"]
        if m.get("arg_type"):
            _out_ann = T.array_param_annotation(
                m["return_type"], writable=True
            )
            _def(
                f"{backing}_{name}",
                # gh-1724: the declared dtypes, by the one helper; the
                # return is the ndarray view of `out`.
                f"def {backing}_{name}(state: {state_t}, "
                f"x: {T.array_param_annotation(m['arg_type'])}, "
                f"out: {_out_ann}) -> NDArray[Any]:",
            )
        else:
            _def(
                f"{backing}_{name}",
                f"def {backing}_{name}(state: {state_t}) -> None:",
            )

    _def(
        f"{backing}_destroy",
        f"def {backing}_destroy(state: {state_t}) -> None:",
    )

    for p in C.module_properties(cfg, module):
        pn, pt = p["name"], p["type"]
        _def(
            f"{backing}_get_{pn}",
            f"def {backing}_get_{pn}(state: {state_t}) -> {_pyi_scalar(pt)}:",
        )
        if p.get("writable"):
            _def(
                f"{backing}_set_{pn}",
                f"def {backing}_set_{pn}(state: {state_t}, "
                f"value: {_pyi_scalar(pt)}) -> None:",
            )
    lines.append("")
    # gh-747: same door as every other `.pyi` producer. No capsule module in
    # doppler overflows today, so this is prevention rather than a fix —
    # which is exactly why it was the half most likely to be left out.
    from ._pyfmt import reflow_pyi
    from ._stubs import prune_stub_imports

    return reflow_pyi("\n".join(prune_stub_imports(lines)))


# ── materialization (driven by jm apply's _replay) ───────────────────────────


def materialize(cfg: dict, root: Path, module: str) -> None:
    """Write a capsule module's generated files into *root* (a project tree).

    Emits the binding ``<cname>_ext.c``, the module ``CMakeLists.txt``, and the
    ``.pyi`` stub, then wires ``add_subdirectory`` into the top ``CMakeLists.txt``
    under the ``# ── Modules`` sentinel. Mirrors ``_module.run`` for an
    object-group module, but for the capsule shape (no ``_core`` / no per-object
    scaffolding). Called from ``_apply._replay`` against the throwaway temp tree;
    ``jm apply`` then syncs the three glue files onto the real project.
    """
    from ._init import _write

    require_scope_names(arg_scopes(cfg, module))  # gh-1525
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

    # Wire the top CMakeLists add_subdirectory (Modules sentinel), like
    # _module.run does for an object-group module.
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
            _textio.write_text(cmake_path, text)
