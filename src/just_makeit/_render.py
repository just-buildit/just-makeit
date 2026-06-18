"""
_render.py — template loading and rendering for just-makeit.

Templates live in src/just_makeit/templates/ as real files. This module
loads them at import time and exposes them as module-level constants with
the same names callers already use (COMPONENT_CORE_H, CMAKE_LISTS_TOP, etc.).
"""

from __future__ import annotations

from pathlib import Path

from ._types import (
    _CTYPE_META,
    _CTYPE_TO_NPY,
    _PYBUILD_FMT,
    _ctype_display,
    _join_fmt_with_optional,
    array_elem_ctype,
    is_array_param_type,
    parse_out_type,
)
from . import _coerce
from . import _config as C

_TMPL_DIR = Path(__file__).parent / "templates"


def _load(relpath: str) -> str:
    return (_TMPL_DIR / relpath).read_text(encoding="utf-8")


# ── C headers ────────────────────────────────────────────────────────────────
CLIB_COMMON_H = _load("c/inc/clib_common.h")
PYEX_COMMON_H = _load("c/inc/pyex_common.h")
JM_SIMD_H = _load("c/inc/jm_simd.h")
JM_PERF_H = _load("c/inc/jm_perf.h")
JM_BENCH_H = _load("c/inc/jm_bench.h")
COMPONENT_CORE_H = _load("c/inc/component_core.h")
MODULE_CORE_H = _load("c/inc/module_core.h")
UMBRELLA_H = _load("c/inc/umbrella.h")
# Seeded into a new project only when `--c-style clang-format` is requested
# (gh-265), so `clang-format --style=file` has the house style to format to.
CLANG_FORMAT = _load("c/.clang-format")
# ── C source ─────────────────────────────────────────────────────────────────
COMPONENT_CORE_C = _load("c/src/component_core.c")
COMPONENT_EXT_C = _load("c/src/component_ext.c")
COMPONENT_TEST_C = _load("c/src/component_test.c")
COMPONENT_BENCH_C = _load("c/src/component_bench.c")
NO_STEP_BENCH_C = _load("c/src/no_step_bench.c")
MODULE_CORE_C = _load("c/src/module_core.c")
LIB_STUB_C = _load("c/src/lib_stub.c")
# ── CMake ────────────────────────────────────────────────────────────────────
CMAKE_LISTS_TOP = _load("cmake/CMakeLists_top.cmake")
CMAKE_LISTS_MODULE = _load("cmake/CMakeLists_module.cmake")
CMAKE_LISTS_OBJECT_CORE = _load("cmake/CMakeLists_object_core.cmake")
CMAKE_LISTS_COMPONENT = _load("cmake/CMakeLists_component.cmake")
CMAKE_PC_IN = _load("cmake/package.pc.in")
CMAKE_CONFIG_IN = _load("cmake/packageConfig.cmake.in")
# ── CI workflows ───────────────────────────────────────────────────────────────
CI_GITHUB = _load("ci/github.yml")
CI_WOODPECKER = _load("ci/woodpecker.yml")
# ── Make ─────────────────────────────────────────────────────────────────────
MAKEFILE = _load("make/Makefile")
MAKEFILE_SIMPLE = _load("make/Makefile_simple")
MAKEFILE_SIMPLE_COMPONENT = _load("make/Makefile_simple_component")
# ── Doc ──────────────────────────────────────────────────────────────────────
DOXYFILE = _load("doc/Doxyfile")
DOCS_INDEX_MD = _load("doc/docs_index.md")
DOCS_API_MD = _load("doc/docs_api.md")
README_MD = _load("doc/README.md")
# ── TOML / config ────────────────────────────────────────────────────────────
ZENSICAL_TOML = _load("toml/zensical.toml")
PYPROJECT_TOML = _load("toml/pyproject.toml")
JB_TOML = _load("toml/jb.toml")
# ── Misc ─────────────────────────────────────────────────────────────────────
GITIGNORE = _load("misc/.gitignore")
# ── Python ───────────────────────────────────────────────────────────────────
MODULE_INIT_PY = _load("py/module_init.py")
MODULE_INIT_PY_EMPTY = _load("py/module_init_empty.py")
SUBPACKAGE_INIT_PY = _load("py/subpackage_init.py")
PACKAGE_INIT_PY = _load("py/package_init.py")
PACKAGE_INIT_PY_MINIMAL = _load("py/package_init_minimal.py")
COMPONENT_PYI = _load("py/component.pyi")
PYTEST_TEST = _load("py/pytest_test.py")
MODULE_PYTEST_TEST = _load("py/module_pytest_test.py")
PYTEST_TEST_PURE = _load("py/pytest_test_pure.py")
MODULE_PYTEST_TEST_PURE = _load("py/module_pytest_test_pure.py")
COMPONENT_BENCH_PY = _load("py/component_bench.py")
COMPONENT_BENCH_PYTEST_BM = _load("py/component_bench_pytest_bm.py")
MODULE_BENCH_PY = _load("py/module_bench.py")
MODULE_BENCH_PYTEST_BM = _load("py/module_bench_pytest_bm.py")
# ── App scaffolds ────────────────────────────────────────────────────────────
APP_MAIN_C = _load("c/app_main.c")
APP_CONSOLE_CLI = _load("py/app_console_cli.py")
APP_PEP723 = _load("py/app_pep723.py")
APP_MAIN_FN_C = _load("c/app_main_fn.c")
APP_CONSOLE_CLI_FN = _load("py/app_console_cli_fn.py")
APP_PEP723_FN = _load("py/app_pep723_fn.py")
APP_MAIN_CMD_C = _load("c/app_main_cmd.c")
APP_CONSOLE_CLI_CMD = _load("py/app_console_cli_cmd.py")
APP_PEP723_CMD = _load("py/app_pep723_cmd.py")
# Empty tests package init — written as a blank __init__.py.
TESTS_INIT_PY = ""


def render(template: str, ctx: dict) -> str:
    result = template
    for k, v in ctx.items():
        if not isinstance(v, str):
            continue
        result = result.replace(f"/*<<{k}>>*/", v)
        result = result.replace(f"<<{k}>>", v)
    return result


# ── Multi-object module support ──────────────────────────────────────────────
#
# A "module" is a single .so that hosts multiple Python types ("objects").
# COMPONENT_TYPE_SECTION is the per-object block (struct + methods +
# PyTypeObject) without file headers or PyMODINIT_FUNC.
# MODULE_EXT_C is the full file: header + <<type_sections>> + PyMODINIT_FUNC.
# render_module_ext_c() assembles the two from a list of component contexts.
#
# <<module>> must be in the ctx passed to COMPONENT_TYPE_SECTION; it equals
# the component name for standalone components, or the module name otherwise.

COMPONENT_TYPE_SECTION = """\
/* ======================================================== */
/* <<Component>>Object — wraps <<component>>_state_t *       */
/* ======================================================== */

#include "<<component>>/<<component>>_core.h"

typedef struct {
    PyObject_HEAD
    <<component>>_state_t *handle;
<<extra_buf_fields>>} <<Component>>Object;

static void
<<ComponentW>>_dealloc(<<Component>>Object *self)
{
    if (self->handle)
        <<component>>_destroy(self->handle);
<<extra_buf_free>>    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
<<ComponentW>>_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    <<Component>>Object *self = (<<Component>>Object *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
<<ComponentW>>_init(<<Component>>Object *self, PyObject *args, PyObject *kwds)
{
<<init_parse_block>><<array_args_parse_block>><<create_line>><<array_args_decref>>    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "<<component>>_create returned NULL");
        return -1;
    }
<<extra_buf_alloc>>    return 0;
}

<<builtin_reset_c>>

<<step_ext_fn>>

<<steps_ext_fn>>

<<getter_setter_methods_c>>
<<extra_methods_c>>
<<getset_def>>
static PyObject *
<<ComponentW>>_destroy(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        <<component>>_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
<<ComponentW>>_enter(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
<<ComponentW>>_exit(<<Component>>Object *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        <<component>>_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

<<stream_iter_block>>static PyMethodDef <<ComponentW>>_methods[] = {
<<builtin_reset_pmd>><<step_pymethoddef_entry>><<steps_def_entry>>
<<getter_setter_pymethoddef>><<extra_methods_pymethoddef>><<stream_def_entry>>    {"destroy",  (PyCFunction)<<ComponentW>>_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)<<ComponentW>>_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)<<ComponentW>>_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject <<ComponentW>>Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "<<module_tp>>.<<Component>>",
    .tp_basicsize = sizeof(<<Component>>Object),
    .tp_dealloc   = (destructor)<<ComponentW>>_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = <<tp_doc>>,
    .tp_methods   = <<ComponentW>>_methods,<<tp_getset_decl>><<stream_tp_iter>><<stream_tp_async>>
    .tp_new       = <<ComponentW>>_new,
    .tp_init      = (initproc)<<ComponentW>>_init,
};
"""

MODULE_EXT_C_HEADER = """\
/*
 * <<module>>_ext.c — Python extension module <<module>>
 *
 * Objects: <<object_list>>
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>
<<module_extra_includes>>
<<module_core_include>>"""

MODULE_EXT_C_FOOTER = """\

/* ======================================================== */
/* Module                                                    */
/* ======================================================== */

<<module_methods_def>>static PyModuleDef <<module>>_moduledef = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "<<module_leaf>>",
    .m_doc     = "<<Module>> module.",
    .m_size    = -1,
    .m_methods = <<module_m_methods>>,
};

PyMODINIT_FUNC
PyInit_<<module_leaf>>(void)
{
    import_array();
<<type_ready_checks>>
    PyObject *m = PyModule_Create(&<<module>>_moduledef);
    if (!m) return NULL;
<<add_object_calls>>
    return m;
}
"""


def _fn_c_params(
    params: list[tuple],
) -> tuple[str, str]:
    """Return (c_param_str, suppress_lines) for a list of param tuples.

    Each param is either ``(name, type)`` or ``(name, type, out)`` where the
    optional third element is a bool. Array params ("type[]") expand to
    ``(const elem_t *name, size_t name_len)`` by default; when ``out=True``
    the ``const`` is dropped so the function can write through the pointer
    (gh-72).
    """
    c_parts: list[str] = []
    suppress_parts: list[str] = []
    for p in params:
        n, t = p[0], p[1]
        is_out = bool(p[2]) if len(p) > 2 else False
        if t == "path":
            # gh-353: a path arg crosses as a borrowed PyBytes (the binding
            # coerces with PyUnicode_FSConverter); the C function receives a
            # plain `const char *` it copies during the call (gh-219 UAF).
            c_parts.append(f"{_coerce.PATH_C_TYPE}{n}")
            suppress_parts.append(f"(void){n};")
        elif is_array_param_type(t):
            elem_disp = _ctype_display(array_elem_ctype(t))
            qual = "" if is_out else "const "
            c_parts.append(f"{qual}{elem_disp} *{n}")
            c_parts.append(f"size_t {n}_len")
            suppress_parts.append(f"(void){n};")
            suppress_parts.append(f"(void){n}_len;")
        else:
            c_parts.append(f"{_ctype_display(t)} {n}")
            suppress_parts.append(f"(void){n};")
    c_param_str = ", ".join(c_parts) if c_parts else "void"
    suppress = "    " + " ".join(suppress_parts) if suppress_parts else ""
    return c_param_str, suppress


def _scalar_c_param(p: tuple) -> str:
    """C declaration for one non-array param (out_type / result_fields paths).

    gh-353: a ``path`` arg crosses as a borrowed ``const char *`` (the binding
    coerces it with ``PyUnicode_FSConverter``); an enum arg is already typed
    ``int`` in the manifest, so it needs no special case here.
    """
    n, t = p[0], p[1]
    if t == "path":
        return f"{_coerce.PATH_C_TYPE}{n}"
    return f"{_ctype_display(t)} {n}"


def fn_c_decl(
    fn_name: str,
    params: list[tuple],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    variable_output: bool = False,
) -> str:
    """One-line C declaration: 'return_type fn_name(c_params);'

    out_type: if set, inserts '{out_type} *out' after array params and
    forces the return type to void (output is returned via the pointer).

    variable_output: when set (a #318 self-sizing output), '{out_type} *out'
    is appended LAST instead of after the array params, so the C signature
    matches the binding's call (which appends the self-allocated buffer last).

    result_fields: if set, forces return type to size_t (count) and
    appends '{return_type} *result' (plus 'size_t max_results' when
    max_results_param is empty, meaning the cap is not already a named
    param).
    """
    result_fields = result_fields or []
    if result_fields:
        rt_disp = _ctype_display(return_type)
        c_param_str, _ = _fn_c_params(params)
        extra = f", {rt_disp} *result"
        if not max_results_param:
            extra += ", size_t max_results"
        return f"size_t {fn_name}({c_param_str}{extra});\n"
    if out_type:
        arr_p = [p for p in params if is_array_param_type(p[1])]
        scl_p = [p for p in params if not is_array_param_type(p[1])]
        # gh-128: out_type may carry a [param_name] size annotation
        # (e.g. "float64[M]").  Resolve to the underlying C type so the
        # declaration emits "double *out", not the invalid "float64[M] *out".
        _out_ctype, _ = parse_out_type(out_type)
        out_disp = _ctype_display(_out_ctype)
        c_parts: list[str] = []
        for p in arr_p:
            n, t = p[0], p[1]
            qual = "" if (len(p) > 2 and p[2]) else "const "
            c_parts.append(f"{qual}{_ctype_display(array_elem_ctype(t))} *{n}")
            c_parts.append(f"size_t {n}_len")
        if not variable_output:
            c_parts.append(f"{out_disp} *out")
        for p in scl_p:
            c_parts.append(_scalar_c_param(p))
        if variable_output:
            c_parts.append(f"{out_disp} *out")
        full_params = ", ".join(c_parts) if c_parts else "void"
        return f"void {fn_name}({full_params});\n"
    ret_disp = _ctype_display(return_type)
    c_param_str, _ = _fn_c_params(params)
    return f"{ret_disp} {fn_name}({c_param_str});\n"


def fn_c_inline_stub(
    fn_name: str,
    params: list[tuple],
    return_type: str,
) -> str:
    """C body stub for embedding in ``_core.h`` as ``static inline``.

    Emits the full ``static inline`` definition so callers see the body at
    compile time.  No entry is written to ``_core.c``.  Intended for pure,
    stateless functions that benefit from inlining at every call site.

    Parameters
    ----------
    fn_name : str
        C function name (without module prefix).
    params : list of (name, type)
        Scalar parameters only — array params and out_type are not supported
        for inline functions.
    return_type : str
        C return type string (e.g. ``"int16_t"``, ``"float"``).

    Returns
    -------
    str
        ``static inline`` C source ready to splice into ``_core.h``.

    Examples
    --------
    >>> print(fn_c_inline_stub("clip_f32", [("x", "float"), ("lo", "float")], "float"))
    /* <<IMPLEMENT: clip_f32>> */
    static inline float
    clip_f32(float x, float lo)
    {
        return (float)0.0f; /* placeholder */
    }
    <BLANKLINE>
    """
    ret_disp = _ctype_display(return_type)
    ret_meta = _CTYPE_META.get(return_type)
    c_param_str, suppress = _fn_c_params(params)
    c_ret_line = (
        f"    return ({ret_disp}){ret_meta['zero']}; /* placeholder */"
        if ret_meta
        else ""
    )
    return (
        f"/* <<IMPLEMENT: {fn_name}>> */\n"
        f"static inline {ret_disp}\n"
        f"{fn_name}({c_param_str})\n"
        f"{{\n"
        + (suppress + "\n" if suppress else "")
        + (c_ret_line + "\n" if c_ret_line else "")
        + "}\n"
    )


def fn_c_stub(
    fn_name: str,
    params: list[tuple],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    variable_output: bool = False,
) -> str:
    """C implementation stub for <module>_core.c (public, no _impl suffix).

    out_type, variable_output, and result_fields extend the signature in the
    same way as fn_c_decl; see that function's docstring for the semantics.
    """
    result_fields = result_fields or []
    if result_fields:
        rt_disp = _ctype_display(return_type)
        c_param_str, suppress = _fn_c_params(params)
        extra_params = f", {rt_disp} *result"
        if not max_results_param:
            extra_params += ", size_t max_results"
        suppress_extra = " (void)result;"
        if not max_results_param:
            suppress_extra += " (void)max_results;"
        suppress_line = (
            (suppress + suppress_extra)
            if suppress
            else ("    " + suppress_extra.strip())
        )
        return (
            f"/* <<IMPLEMENT: {fn_name}>> */\n"
            f"size_t\n"
            f"{fn_name}({c_param_str}{extra_params})\n"
            f"{{\n"
            + suppress_line
            + "\n"
            + "    return 0; /* placeholder */\n"
            + "}\n"
        )
    if out_type:
        arr_p = [p for p in params if is_array_param_type(p[1])]
        scl_p = [p for p in params if not is_array_param_type(p[1])]
        # gh-128: resolve numpy dtype + size annotation → C type.
        _out_ctype, _ = parse_out_type(out_type)
        out_disp = _ctype_display(_out_ctype)
        c_parts: list[str] = []
        suppress_parts: list[str] = []
        for p in arr_p:
            n, t = p[0], p[1]
            qual = "" if (len(p) > 2 and p[2]) else "const "
            c_parts.append(f"{qual}{_ctype_display(array_elem_ctype(t))} *{n}")
            c_parts.append(f"size_t {n}_len")
            suppress_parts += [f"(void){n};", f"(void){n}_len;"]
        if not variable_output:
            c_parts.append(f"{out_disp} *out")
            suppress_parts.append("(void)out;")
        for p in scl_p:
            n = p[0]
            c_parts.append(_scalar_c_param(p))
            suppress_parts.append(f"(void){n};")
        if variable_output:
            c_parts.append(f"{out_disp} *out")
            suppress_parts.append("(void)out;")
        full_params = ", ".join(c_parts) if c_parts else "void"
        suppress = "    " + " ".join(suppress_parts) if suppress_parts else ""
        return (
            f"/* <<IMPLEMENT: {fn_name}>> */\n"
            f"void\n"
            f"{fn_name}({full_params})\n"
            f"{{\n" + (suppress + "\n" if suppress else "") + "}\n"
        )
    ret_disp = _ctype_display(return_type)
    ret_meta = _CTYPE_META.get(return_type)
    c_param_str, suppress = _fn_c_params(params)
    c_ret_line = (
        f"    return ({ret_disp}){ret_meta['zero']}; /* placeholder */"
        if ret_meta
        else ""
    )
    return (
        f"/* <<IMPLEMENT: {fn_name}>> */\n"
        f"{ret_disp}\n"
        f"{fn_name}({c_param_str})\n"
        f"{{\n"
        + (suppress + "\n" if suppress else "")
        + (c_ret_line + "\n" if c_ret_line else "")
        + "}\n"
    )


def _build_params_parse(
    params: list[dict],
) -> tuple[str, str, str]:
    """Build parse block + C call args + cleanup for a named multi-param method.

    params: list of {"name": str, "type": str}
      Scalar types come from _CTYPE_META.
      Array types end with '[]', e.g. "float _Complex[]"; their element type
      must be in _CTYPE_TO_NPY.  Array params expand to two C args:
      (const elem_t *name, size_t name_len).

    Returns (parse_block, call_args_c, cleanup):
      parse_block  — indented C code: a kwlist, declarations,
                     PyArg_ParseTupleAndKeywords (positional-or-keyword), array
                     conversion and error-exit paths with partial cleanup. The
                     caller's wrapper must take a ``PyObject *kwds`` parameter.
      call_args_c  — comma-sep C variables/expressions for the downstream call
      cleanup      — Py_DECREF lines for all acquired numpy arrays (empty string
                     when no array params); caller must emit before every return
    """
    decl_lines: list[str] = []  # before PyArg_ParseTuple
    addr_exprs: list[str] = []  # &name args for PyArg_ParseTuple
    fmt_chars: list[str] = []  # format characters
    conv_lines: list[str] = []  # after PyArg_ParseTuple (scalars needing to_c)
    arr_acq: list[str] = []  # array acquisition lines (after ParseTuple)
    call_args: list[str] = []  # final C args to pass
    arr_names: list[str] = []  # arr variable names for Py_DECREF cleanup
    # gh-353: borrowed path PyBytes (from PyUnicode_FSConverter). DECREF'd only
    # AFTER the C call (the C side copies the string during the call — gh-219
    # UAF), and on every pre-call error path before returning NULL.
    path_names: list[str] = []

    for p in params:
        pname = p["name"]
        ptype = p["type"]

        if ptype == "path":
            # gh-353: the shared file-handler pattern (_coerce) — a
            # str | os.PathLike coerces with O& + PyUnicode_FSConverter into a
            # borrowed PyBytes; pass PyBytes_AS_STRING to the const char * arg.
            # Same primitives the handle generator uses for a `path` create-arg.
            decl_lines.append("    " + _coerce.path_decl(pname))
            fmt_chars.append(_coerce.path_fmt())
            addr_exprs.append(_coerce.path_addr(pname))
            path_names.append(pname)
            call_args.append(_coerce.path_call_expr(pname))
        elif p.get("enum"):
            # gh-353 (mirrors _handle's enum-validate in render_tp_init): parse
            # the choice string with `s`, validate to its SSOT int via
            # _enum_index; a `< 0` raises ValueError after cleaning up any
            # arrays / path objects acquired so far, then pass the validated int.
            ename = p["enum"]
            fmt_chars.append("s")
            # gh-240/gh-353: a defaulted enum is optional — its C local seeds to
            # the default choice string so an omitted arg validates to that
            # choice's index. A required enum seeds to "" (an invalid choice,
            # but PyArg fills it before _enum_index runs).
            _edflt = p.get("default") or ""
            decl_lines.append(f'    const char *{pname} = "{_edflt}";')
            addr_exprs.append(f"&{pname}")
            prior = "".join(f" Py_DECREF({a});" for a in arr_names) + "".join(
                f" {_coerce.path_release(n)}" for n in path_names
            )
            conv_lines.append(
                f"    int _arg_{pname} = _enum_index(_enum_{ename}, {pname});\n"
                f"    if (_arg_{pname} < 0) {{\n"
                f'        PyErr_Format(PyExc_ValueError, "invalid {pname}'
                f" '%s'\", {pname});{prior}\n"
                f"        return NULL;\n"
                f"    }}"
            )
            call_args.append(f"_arg_{pname}")
        elif is_array_param_type(ptype):
            elem_ct = array_elem_ctype(ptype)
            npy_enum = _CTYPE_TO_NPY[elem_ct]
            elem_disp = _ctype_display(elem_ct)
            obj_var = f"{pname}_obj"
            arr_var = f"{pname}_arr"

            decl_lines.append(f"    PyObject *{obj_var} = NULL;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")

            # Build error path: decref all arrays + path objects (gh-353)
            # acquired so far.
            prior_decrefs = "".join(
                f" Py_DECREF({a});" for a in arr_names
            ) + "".join(f" {_coerce.path_release(n)}" for n in path_names)
            is_out = bool(p.get("out"))
            npy_flags = (
                "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE"
                if is_out
                else "NPY_ARRAY_C_CONTIGUOUS"
            )
            const_qual = "" if is_out else "const "
            arr_acq.append(
                f"    PyArrayObject *{arr_var} = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        {obj_var}, {npy_enum}, {npy_flags});\n"
                f"    if (!{arr_var}) {{{prior_decrefs} return NULL; }}"
            )
            arr_acq.append(
                f"    {const_qual}{elem_disp} *{pname} = "
                f"({const_qual}{elem_disp} *)PyArray_DATA({arr_var});\n"
                f"    size_t {pname}_len = (size_t)PyArray_SIZE({arr_var});"
            )
            arr_names.append(arr_var)
            call_args.extend([pname, f"{pname}_len"])
        else:
            meta = _CTYPE_META[ptype]
            disp = _ctype_display(ptype)
            fmt_chars.append(meta["fmt"])

            if "parse_type" in meta:
                raw = f"{pname}_raw"
                decl_lines.append(
                    f"    {meta['parse_type']} {raw} = {meta['parse_zero']};"
                )
                addr_exprs.append(f"&{raw}")
                conv_lines.append(
                    f"    {disp} {pname} = {meta['to_c'](pname)};"
                )
            else:
                # gh-240: a scalar with a `default` is optional — its C local is
                # initialised to the default literal so an omitted arg yields it
                # (PyArg leaves it untouched). Required scalars init to zero.
                init = p.get("default") or meta["zero"]
                decl_lines.append(f"    {disp} {pname} = {init};")
                addr_exprs.append(f"&{pname}")

            call_args.append(pname)

    # gh-240: split required vs optional. A param with a `default` is optional;
    # the `|` in the PyArg format goes before the first optional param. Optional
    # params must follow all required ones (the PyArg `|` rule == Python's
    # "no required parameter after a defaulted one"); validate and error clearly.
    fmt_str = _join_fmt_with_optional(fmt_chars, params)
    addr_str = ", ".join(addr_exprs)
    # gh-238: module functions are positional-OR-keyword. Each param name is a
    # kwarg (an array param's kwarg is its object), and the kwlist order matches
    # the fmt/addr order. Keyword *capability* is ~free when callers still pass
    # positionally; the keyword-match cost is paid only when keywords are used —
    # the right trade for the (often multi-param) function call site, which is
    # rarely the innermost loop. The per-sample hot path (step/steps) stays
    # positional-only.
    kwnames = "".join(f'"{p["name"]}", ' for p in params)
    # gh-353: a parse failure may have already converted some O& path args (and
    # FSConverter sets the target on success), so XDECREF every path object on
    # the parse-fail path before returning NULL. The multi-statement cleanup is
    # braced (the no-path form keeps the bare `return NULL;` — zero churn).
    parse_fail = (
        "        return NULL;"
        if not path_names
        else "    {\n"
        + "".join(f"        {_coerce.path_release(n)}\n" for n in path_names)
        + "        return NULL;\n    }"
    )
    lines = (
        [f"    static char *_kwlist[] = {{{kwnames}NULL}};"]
        + decl_lines
        + [
            f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt_str}",',
            f"            _kwlist, {addr_str}))",
            parse_fail,
        ]
        + conv_lines
        + arr_acq
    )
    # The final cleanup (emitted by callers AFTER the C call): array DECREFs +
    # path XDECREFs (gh-353 — the C side has copied the string by now).
    cleanup = "".join(f"    Py_DECREF({a});\n" for a in arr_names) + "".join(
        f"    {_coerce.path_release(n)}\n" for n in path_names
    )
    return "\n".join(lines) + "\n", ", ".join(call_args), cleanup


def _py_wrapper_for_function(
    fn_name: str,
    params: list[dict],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    variable_output: bool = False,
    out_size: str = "",
) -> str:
    """Generate a _bind_<fn_name> Python wrapper for a module-level C function.

    The C function is assumed to be declared in <module>_core.h and named
    exactly fn_name (public, no prefix).

    out_type: if set, allocates a 1-D ndarray of this type (length = first
    array param's length) and passes it after the array args, before scalars.

    result_fields + max_results_param: if set, calls C with a stack-allocated
    array of structs, builds and returns list[tuple] from the fields.
    """
    result_fields = result_fields or []
    ret_meta = _CTYPE_META.get(return_type)

    if params:
        parse_block, call_args, cleanup = _build_params_parse(params)
        # Positional-or-keyword (gh-238): the binding takes kwds and parses with
        # PyArg_ParseTupleAndKeywords. A no-param function stays METH_NOARGS.
        py_args = "PyObject *args, PyObject *kwds"
    else:
        parse_block = ""
        call_args = ""
        cleanup = ""
        py_args = "PyObject *Py_UNUSED(args)"

    if result_fields and max_results_param:
        # Build list-of-tuples from struct array.
        _rf_fmt_parts: list[str] = []
        _rf_arg_parts: list[str] = []
        for _rf in result_fields:
            _rft = _rf["type"]
            _rfn = _rf["name"]
            _fmt_c, _cast = _PYBUILD_FMT.get(_rft, ("i", ""))
            _rf_fmt_parts.append(_fmt_c)
            _val = f"_results[_i].{_rfn}"
            if _cast:
                _val = f"({_cast}){_val}"
            _rf_arg_parts.append(_val)
        _bvfmt = '"(' + "".join(_rf_fmt_parts) + ')"'
        _bvargs = ", ".join(_rf_arg_parts)
        _rt_disp = _ctype_display(return_type)
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        ret_line = (
            f"    size_t _max = (size_t){max_results_param};\n"
            f"    {_rt_disp} *_results ="
            f" ({_rt_disp} *)malloc(_max * sizeof({_rt_disp}));\n"
            f"    if (!_results) {{{_cleanup_inline} return PyErr_NoMemory(); }}\n"
            f"    size_t _n = {fn_name}({call_args}, _results);\n"
            f"{cleanup}"
            f"    PyObject *_lst = PyList_New((Py_ssize_t)_n);\n"
            f"    if (!_lst) {{ free(_results); return NULL; }}\n"
            f"    for (size_t _i = 0; _i < _n; _i++) {{\n"
            f"        PyObject *_tup = Py_BuildValue({_bvfmt}, {_bvargs});\n"
            f"        if (!_tup) {{ free(_results); Py_DECREF(_lst); return NULL; }}\n"
            f"        PyList_SET_ITEM(_lst, (Py_ssize_t)_i, _tup);\n"
            f"    }}\n"
            f"    free(_results);\n"
            f"    return _lst;"
        )
    elif variable_output and out_type:
        # #318: stateless self-sizing output — the function allocates its own
        # 1-D output (no caller buffer, no cached instance buffer). Its length
        # is `out_size` (a verbatim-C expr over the args + array `<name>_len`s,
        # e.g. "wfm_rrc_ntaps(sps, span)" or "x_len * factor"), or the first
        # array param's length. `out` is appended LAST to the call. If the fn
        # reports a size_t count, trim to it (the runtime-sized shape); a void
        # fn returns the full allocation. Keeps a `rrc_taps(...) -> ndarray`
        # zero-Python (the helper used to allocate internally in hand-Python).
        _base_ctype, _ = parse_out_type(out_type)
        out_npy = _CTYPE_TO_NPY[_base_ctype]
        out_disp = _ctype_display(_base_ctype)
        if out_size:
            len_expr = out_size
        else:
            first_arr = next(
                (p["name"] for p in params if is_array_param_type(p["type"])),
                None,
            )
            len_expr = f"{first_arr}_len" if first_arr else "1"
        _out_ptr = f"({out_disp} *)PyArray_DATA((PyArrayObject *)_out)"
        _call_with_out = f"{call_args}, {_out_ptr}" if call_args else _out_ptr
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        _trim = bool(ret_meta) and ret_meta.get("kind") == "int"
        _alloc = (
            f"    npy_intp _dim = (npy_intp)({len_expr});\n"
            f"    PyObject *_out ="
            f" PyArray_EMPTY(1, &_dim, {out_npy}, 0);\n"
            f"    if (!_out) {{{_cleanup_inline} return NULL; }}\n"
        )
        if _trim:
            ret_line = (
                _alloc
                + f"    size_t _n = (size_t){fn_name}({_call_with_out});\n"
                + cleanup
                + "    PyArray_DIMS((PyArrayObject *)_out)[0] ="
                " (npy_intp)_n;\n"
                "    return _out;"
            )
        else:
            ret_line = (
                _alloc
                + f"    {fn_name}({_call_with_out});\n"
                + cleanup
                + "    return _out;"
            )
    elif out_type:
        # Allocate output array, insert after array args, before scalars.
        # out_type may carry a [param_name] suffix naming the scalar that
        # holds the output length (e.g. "float64[M]").
        _base_ctype, _scalar_len_param = parse_out_type(out_type)
        out_npy = _CTYPE_TO_NPY[_base_ctype]
        out_disp = _ctype_display(_base_ctype)
        if _scalar_len_param:
            len_expr = _scalar_len_param
        else:
            first_arr = next(
                (p["name"] for p in params if is_array_param_type(p["type"])),
                None,
            )
            len_expr = f"{first_arr}_len" if first_arr else "1"
        # call_args is: arr_ptr, arr_len, [more_arr_ptr, arr_len,] scalar1, ...
        # Insert `out` after the last (ptr, len) pair.
        _arr_count = sum(1 for p in params if is_array_param_type(p["type"]))
        _arr_args = call_args.split(", ")
        # Each array expands to 2 args; scalars are single.
        _insert_idx = _arr_count * 2
        _parts_before = ", ".join(_arr_args[:_insert_idx])
        _parts_after = ", ".join(_arr_args[_insert_idx:])
        _sep_before = ", " if _parts_before else ""
        _sep_after = ", " if _parts_after else ""
        _call_with_out = (
            f"{_parts_before}{_sep_before}"
            f"({out_disp} *)PyArray_DATA"
            f"((PyArrayObject *)_out){_sep_after}{_parts_after}"
        )
        _cleanup_inline = cleanup.replace("\n    ", " ").strip()
        ret_line = (
            f"    npy_intp _dim = (npy_intp){len_expr};\n"
            f"    PyObject *_out ="
            f" PyArray_EMPTY(1, &_dim, {out_npy}, 0);\n"
            f"    if (!_out) {{{_cleanup_inline} return NULL; }}\n"
            f"    {fn_name}({_call_with_out});\n"
            f"{cleanup}"
            f"    return _out;"
        )
    elif ret_meta:
        # gh-353: a path arg borrows a PyBytes that the C call copies, so the
        # path XDECREF in `cleanup` must run AFTER the call, not before. Capture
        # the C result into a temp, clean up, then convert + return. (Without a
        # path arg `cleanup` is array-only and order is immaterial — the legacy
        # one-line form is kept so enum-free output is byte-identical.)
        _has_path = any(p["type"] == "path" for p in params)
        if _has_path and cleanup:
            _rt_disp = _ctype_display(return_type)
            ret_line = (
                f"    {_rt_disp} _r = {fn_name}({call_args});\n"
                f"{cleanup}"
                f"    return {ret_meta['to_py']('_r')};"
            )
        else:
            ret_expr = ret_meta["to_py"](f"{fn_name}({call_args})")
            ret_line = f"{cleanup}    return {ret_expr};"
    else:
        call_line = (
            f"    {fn_name}({call_args});" if params else f"    {fn_name}();"
        )
        ret_line = call_line + f"\n{cleanup}    Py_RETURN_NONE;"

    return (
        f"static PyObject *\n"
        f"_bind_{fn_name}(PyObject *self, {py_args})\n"
        f"{{\n"
        f"    (void)self;\n" + parse_block + f"{ret_line}\n" + "}\n"
    )


def _functions_enums_used(
    functions: list[dict],
) -> list[str]:
    """Ordered, de-duplicated enum names referenced by any function param.

    gh-353: a module function's enum arg carries ``{enum: "<name>"}`` (mirrors
    the handle convention). The ``_ext.c`` emits the per-enum tables + the
    shared ``_enum_index`` helper only for the enums actually referenced, so an
    enum-free module renders byte-identical output (no helper, no tables).
    """
    seen: list[str] = []
    for fn in functions:
        for p in fn.get("params", []):
            e = p.get("enum")
            if e and e not in seen:
                seen.append(e)
    return seen


def _render_function_enum_tables(
    functions: list[dict], enums: dict[str, list[str]]
) -> str:
    """Emit the per-enum ``_enum_<name>[]`` tables + the shared ``_enum_index``
    for the enums a module's functions reference (gh-353).

    Reuses the composer's enum SSOT exactly (``_composer._ENUM_INDEX_FN`` and
    the "order is the C int" table layout) — the same contract the handle
    generator's :func:`_handle.render_enum_tables` follows."""
    from ._composer import _ENUM_INDEX_FN

    parts = [_ENUM_INDEX_FN]
    for name in _functions_enums_used(functions):
        values = enums.get(name, [])
        items = "".join(f'    "{v}",\n' for v in values)
        parts.append(f"static const char *const _enum_{name}[] = {{")
        parts.append(items + "    NULL,")
        parts.append("};")
        parts.append("")
    return "\n".join(parts)


def make_functions_ctx(
    module: str,
    Module: str,
    functions: list[dict],
    enums: "dict[str, list[str]] | None" = None,
) -> dict:
    """Return template context keys for module-level Python wrapper functions.

    Returns keys consumed by render_module_ext_c:
      function_wrappers  — static _bind_<fn> functions (inserted after header)
      module_methods_def — static PyMethodDef array block, or ''
      module_m_methods   — '{module}_module_methods' or 'NULL'
      function_enum_tables — the _enum_index helper + per-enum tables (gh-353),
                             or '' when no function param uses an enum
      function_uses_enum — True when any function param references an enum (the
                           ext.c then also #includes <string.h> for strcmp)

    The module-level table is named ``{module}_module_methods`` (not
    ``{Module}_methods``) so it never collides with an object's own
    ``{Component}_methods`` table when the module shares a name with one of
    its objects (the collocated case, e.g. ``jm module fft`` +
    ``jm object fft --module fft``): both end up in the same translation unit
    via the aggregator's ``#include``.
    """
    if not functions:
        return {
            "function_wrappers": "",
            "module_methods_def": "",
            "module_m_methods": "NULL",
            "function_enum_tables": "",
            "function_uses_enum": False,
        }
    wrappers: list[str] = []
    entries: list[str] = []
    for fn in functions:
        name = fn["name"]
        params = list(fn.get("params", []))
        return_type = fn.get("return_type", "void")
        doc = fn.get("doc", f"{name}.")
        # gh-238: a function with params is positional-or-keyword
        # (METH_VARARGS | METH_KEYWORDS); a no-param function stays METH_NOARGS.
        # The kw-capable binding has the 3-arg PyCFunctionWithKeywords signature,
        # so cast it through `(void *)` in the table (jm's convention; silences
        # -Wcast-function-type).
        if params:
            flags = "METH_VARARGS | METH_KEYWORDS"
            fn_ref = f"(PyCFunction)(void *)_bind_{name}"
        else:
            flags = "METH_NOARGS"
            fn_ref = f"_bind_{name}"
        wrappers.append(
            _py_wrapper_for_function(
                name,
                params,
                return_type,
                out_type=fn.get("out_type", ""),
                result_fields=fn.get("result_fields", []),
                max_results_param=fn.get("max_results_param", ""),
                variable_output=bool(fn.get("variable_output")),
                out_size=fn.get("out_size", ""),
            )
        )
        entries.append(f'    {{"{name}", {fn_ref}, {flags}, "{doc}"}},')
    entries.append("    {NULL, NULL, 0, NULL}")
    array_body = "\n".join(entries)
    methods_def = (
        f"static PyMethodDef {module}_module_methods[] = "
        f"{{\n{array_body}\n}};\n\n"
    )
    # gh-353: when a function param references a [[enum]], emit the SSOT
    # _enum_index helper + per-enum tables BEFORE the _bind_ wrappers (which
    # call _enum_index). Enum-free modules get an empty string (no churn).
    enums_used = _functions_enums_used(functions)
    enum_tables = (
        _render_function_enum_tables(functions, enums or {})
        if enums_used
        else ""
    )
    return {
        "function_wrappers": "\n".join(wrappers),
        "module_methods_def": methods_def,
        "module_m_methods": f"{module}_module_methods",
        "function_enum_tables": enum_tables,
        "function_uses_enum": bool(enums_used),
    }


def render_module_ext_c(
    module: str,
    comp_ctxs: list[dict],
    functions: list[dict] = (),
    enums: "dict[str, list[str]] | None" = None,
) -> str:
    """Render a multi-object module _ext.c from a list of component contexts.

    Each ctx must contain 'module' = module_name and 'Component' = the type name.
    Pass functions (from config module_functions()) to wire up module-level
    PyMethodDef entries; Python wrappers are emitted inline (not via #include).
    Pass enums (from ``C.enums(cfg)``) so a function's ``enum`` param emits the
    SSOT ``_enum_index`` helper + per-enum tables (gh-353).

    ``module`` may be a dotted id (``dsp.filters``); the C identifiers /
    file-name prefixes use the cname form (``dsp_filters``) while the
    ``PyInit_``/``.m_name`` use the leaf (``filters``). For a dotless id all
    three coincide, so flat modules render unchanged.
    """
    mp = C.module_paths(module)
    leaf = mp.leaf
    module = mp.cname
    Module = "".join(w.title() for w in module.split("_"))
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)

    fn_ctx = make_functions_ctx(module, Module, list(functions), enums)
    # Only include the module-level core header when there are module functions
    # that use it.  Objects have their own per-component includes in
    # COMPONENT_TYPE_SECTION; the module_core.h is only needed when module-
    # level C functions (declared in module_core.h) are wired into the ext.c.
    has_module_fns = bool(functions)
    module_core_include = (
        f'#include "{module}/{module}_core.h"\n' if has_module_fns else ""
    )
    header_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "module_core_include": module_core_include,
        # gh-353: an enum param's _enum_index uses strcmp.
        "module_extra_includes": (
            "#include <string.h>\n" if fn_ctx.get("function_uses_enum") else ""
        ),
    }
    parts = [render(MODULE_EXT_C_HEADER, header_ctx)]

    if fn_ctx.get("function_enum_tables"):
        parts.append(fn_ctx["function_enum_tables"] + "\n")
    if fn_ctx["function_wrappers"]:
        parts.append(fn_ctx["function_wrappers"] + "\n")

    for ctx in comp_ctxs:
        parts.append(render(COMPONENT_TYPE_SECTION, ctx))

    type_ready_lines: list[str] = []
    for ctx in comp_ctxs:
        type_ready_lines.append(
            f"    if (PyType_Ready(&{ctx['ComponentW']}Type) < 0) return NULL;"
        )
        # gh-203: a streamable object also readies its iterator type.
        if ctx.get("stream_module_ready"):
            type_ready_lines.append(ctx["stream_module_ready"])
    type_ready_checks = "\n".join(type_ready_lines)
    add_object_calls_lines: list[str] = []
    for ctx in comp_ctxs:
        C_ = ctx["Component"]
        CW_ = ctx["ComponentW"]
        add_object_calls_lines += [
            f"    Py_INCREF(&{CW_}Type);",
            f'    if (PyModule_AddObject(m, "{C_}", (PyObject *)&{CW_}Type) < 0) {{',
            f"        Py_DECREF(&{CW_}Type); Py_DECREF(m); return NULL;",
            "    }",
        ]
    add_object_calls = "\n".join(add_object_calls_lines)

    footer_ctx = {
        "module": module,
        "module_leaf": leaf,
        "Module": Module,
        "type_ready_checks": type_ready_checks,
        "add_object_calls": add_object_calls,
        **fn_ctx,
    }
    parts.append(render(MODULE_EXT_C_FOOTER, footer_ctx))
    return "".join(parts)


_FRAGMENT_FILE_HEADER = """\
/*
 * <<module>>_ext_<<component>>.c — <<Component>> type for the <<module>> module.
 *
 * Included by <<module>>_ext.c (the module aggregator).
 * Hand-patches to this file are preserved across jm commands.
 * Do NOT compile this file directly — only <<module>>_ext.c is compiled.
 */
"""


def render_module_ext_fragment(comp_ctx: dict) -> str:
    """Render the per-object section for one fragment file.

    The returned text is the content of ``<module>_ext_<comp>.c``: a brief
    warning header followed by the full ``COMPONENT_TYPE_SECTION`` for the
    object.  It contains no Python.h include (the aggregator provides it).
    """
    header = render(_FRAGMENT_FILE_HEADER, comp_ctx)
    return header + render(COMPONENT_TYPE_SECTION, comp_ctx)


def render_module_ext_aggregator(
    module: str,
    comp_ctxs: list[dict],
    functions: list[dict] = (),
    extra_files: "set[str] | frozenset[str]" = frozenset(),
    extra_types: "list[str] | None" = None,
    enums: "dict[str, list[str]] | None" = None,
) -> str:
    """Render the thin aggregator ``<module>_ext.c``.

    The aggregator #includes each per-object fragment in order, then defines
    the module-level PyModuleDef and PyInit_.  It is always overwritten on
    regeneration; hand-written code belongs in the fragment files or in
    never-touched ``*_extra.c`` files.

    Parameters
    ----------
    extra_files : set of str
        Basenames of ``*_extra.c`` files that exist on disk (e.g.
        ``{"filter_ext_fir_extra.c", "filter_ext_extra.c"}``).
        Per-object extras are included immediately after their fragment;
        the per-module extra is included after all fragments.
        jm never creates or modifies these files.
    extra_types : list of str, optional
        Names of hand-written CPython types declared in ``*_extra.c`` files
        that should be registered in ``PyInit_<module>``.  For each name
        ``T``, jm emits ``PyType_Ready(&TType)`` and
        ``PyModule_AddObject(m, "T", (PyObject *)&TType)``.
    """
    mp = C.module_paths(module)
    leaf = mp.leaf
    module = mp.cname
    Module = "".join(w.title() for w in module.split("_"))
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)
    fn_ctx = make_functions_ctx(module, Module, list(functions), enums)
    has_module_fns = bool(functions)
    module_core_include = (
        f'#include "{module}/{module}_core.h"\n' if has_module_fns else ""
    )
    header_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "module_core_include": module_core_include,
        # gh-353: an enum param's _enum_index uses strcmp.
        "module_extra_includes": (
            "#include <string.h>\n" if fn_ctx.get("function_uses_enum") else ""
        ),
    }
    parts = [render(MODULE_EXT_C_HEADER, header_ctx)]
    # Replace the "Objects: ..." comment to clarify this is the aggregator.
    parts[0] = parts[0].replace(
        f" * Objects: {object_list}",
        f" * Objects: {object_list}\n"
        f" * GENERATED — do not hand-edit. Patches belong in the _ext_<obj>.c fragments.",
    )
    # Include each per-object fragment, then its per-object extra if present.
    include_parts: list[str] = []
    for ctx in comp_ctxs:
        comp = ctx["component"]
        include_parts.append(f'#include "{module}_ext_{comp}.c"')
        obj_extra = f"{module}_ext_{comp}_extra.c"
        if obj_extra in extra_files:
            include_parts.append(
                f'#include "{obj_extra}"  /* hand-written — jm never modifies */'
            )
    # Per-module extra goes after all fragments.
    mod_extra = f"{module}_ext_extra.c"
    if mod_extra in extra_files:
        include_parts.append(
            f'#include "{mod_extra}"  /* hand-written — jm never modifies */'
        )
    parts.append("\n" + "\n".join(include_parts) + "\n")
    if fn_ctx.get("function_enum_tables"):
        parts.append("\n" + fn_ctx["function_enum_tables"] + "\n")
    if fn_ctx["function_wrappers"]:
        parts.append("\n" + fn_ctx["function_wrappers"] + "\n")
    _extra_types = extra_types or []
    type_ready_lines: list[str] = []
    for ctx in comp_ctxs:
        type_ready_lines.append(
            f"    if (PyType_Ready(&{ctx['ComponentW']}Type) < 0) return NULL;"
        )
        # gh-203: a streamable object also readies its iterator type.
        if ctx.get("stream_module_ready"):
            type_ready_lines.append(ctx["stream_module_ready"])
    type_ready_lines += [
        f"    if (PyType_Ready(&{et}Type) < 0) return NULL;"
        for et in _extra_types
    ]
    type_ready_checks = "\n".join(type_ready_lines)
    add_object_calls_lines: list[str] = []
    for ctx in comp_ctxs:
        C_ = ctx["Component"]
        CW_ = ctx["ComponentW"]
        add_object_calls_lines += [
            f"    Py_INCREF(&{CW_}Type);",
            f'    if (PyModule_AddObject(m, "{C_}", (PyObject *)&{CW_}Type) < 0) {{',
            f"        Py_DECREF(&{CW_}Type); Py_DECREF(m); return NULL;",
            "    }",
        ]
    for et in _extra_types:
        add_object_calls_lines += [
            f"    Py_INCREF(&{et}Type);",
            f'    if (PyModule_AddObject(m, "{et}", (PyObject *)&{et}Type) < 0) {{',
            f"        Py_DECREF(&{et}Type); Py_DECREF(m); return NULL;",
            "    }",
        ]
    add_object_calls = "\n".join(add_object_calls_lines)
    footer_ctx = {
        "module": module,
        "module_leaf": leaf,
        "Module": Module,
        "type_ready_checks": type_ready_checks,
        "add_object_calls": add_object_calls,
        **fn_ctx,
    }
    parts.append(render(MODULE_EXT_C_FOOTER, footer_ctx))
    return "".join(parts)
