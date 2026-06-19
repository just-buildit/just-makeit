"""Module-function ``check_return`` — raise on a non-zero int status (gh-363).

A status-returning C function (0 = success) can have its binding raise
``RuntimeError`` on a non-zero result instead of returning the int — the
module-function analog of the handle generator's ``close_returns``. Covers the
codegen shape, a compile-and-run proof (0 -> None, non-zero -> raise), and that
it composes with the gh-353 ``path``/``enum`` args. Mirrors
``test_function_path_enum_build``; the build test is skipped without a compiler.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as R
from just_makeit._render import make_functions_ctx
from just_makeit._stubs import _fn_stub


# ── codegen (no compiler) ────────────────────────────────────────────────────


def test_check_return_binding_raises_on_nonzero():
    src = R._py_wrapper_for_function("commit", [], "int", check_return=True)
    assert "int _rc = commit();" in src
    assert "PyErr_Format(PyExc_RuntimeError," in src
    assert '"commit failed (rc=%d)", (int)_rc);' in src
    assert "return NULL;" in src
    assert "Py_RETURN_NONE;" in src
    # the int is never handed back to Python
    assert "PyLong_From" not in src


def test_check_return_pyi_is_none():
    stub = _fn_stub(
        {"name": "commit", "return_type": "int", "check_return": True}
    )
    assert "def commit() -> None:" in stub
    assert "-> int" not in stub


def test_check_return_composes_with_path_and_enum():
    # the write_blue_header shape: path + enum + scalar, raise on non-zero rc.
    src = R._py_wrapper_for_function(
        "write_hdr",
        [
            {"name": "path", "type": "path"},
            {"name": "st", "type": "int", "enum": "stype"},
            {"name": "total", "type": "size_t"},
        ],
        "int",
        check_return=True,
    )
    # path borrow released BEFORE the rc check (after the call), enum validated.
    assert "_enum_index(_enum_stype, st)" in src
    i_call = src.index("int _rc = write_hdr(")
    i_decref = src.index("Py_XDECREF(path);", i_call)
    i_check = src.index("if (_rc != 0)", i_call)
    assert i_call < i_decref < i_check


# ── compile + run ────────────────────────────────────────────────────────────

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

_BACKING = """
#include <stddef.h>
/* Returns its arg as the status: 0 succeeds, non-zero is an error code. */
int do_io(int code) { return code; }
"""

_FNS = [
    {
        "name": "do_io",
        "return_type": "int",
        "check_return": True,
        "params": [{"name": "code", "type": "int"}],
    }
]


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_check_return_compiles_and_raises(tmp_path):
    w = make_functions_ctx("iom", "Iom", _FNS, {})
    src = f"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
{_BACKING}
{w["function_wrappers"]}
{w["module_methods_def"]}
static struct PyModuleDef _md = {{
    PyModuleDef_HEAD_INIT, "iom", NULL, -1, {w["module_m_methods"]},
    NULL, NULL, NULL, NULL
}};
PyMODINIT_FUNC PyInit_iom(void) {{
    import_array();
    return PyModule_Create(&_md);
}}
"""
    c = tmp_path / "iom.c"
    c.write_text(src)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    so = tmp_path / f"iom{suffix}"
    link = (
        ["-bundle", "-undefined", "dynamic_lookup"]
        if sys.platform == "darwin"
        else ["-shared"]
    )
    subprocess.run(
        [
            _CC,
            *link,
            "-fPIC",
            "-O2",
            "-std=c11",
            "-Wall",
            "-Werror",
            "-I",
            sysconfig.get_path("include"),
            "-I",
            np.get_include(),
            str(c),
            "-o",
            str(so),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    spec = importlib.util.spec_from_file_location("iom", so)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # rc 0 -> the call returns None (succeeds-or-raises surface).
    assert mod.do_io(0) is None
    # a non-zero rc raises RuntimeError carrying the code.
    with pytest.raises(RuntimeError, match="do_io failed"):
        mod.do_io(7)
