"""``check_return`` on a self-sizing output raises on a zero count (gh-1704).

A ``variable_output`` function returns the COUNT it wrote, and jm trims the
output to it. ``check_return`` was tested in an ``elif`` after that branch, so
the key was accepted and read by nobody: a refusal trimmed the allocation to
zero and came back as a valid, empty result. For a function whose output is
never legitimately empty, 0 is the only refusal value it has.

Covers: the raise in both self-sizing shapes (ndarray and ``str``), the
refusal of the two shapes that never read the C return (a void
``variable_output`` and a caller-sized ``out_type``), the stub keeping its
output type, and a compile-and-run proof. Mirrors
``test_function_check_return``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import sysconfig
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as R
from just_makeit._render import make_functions_ctx
from just_makeit._stubs import _fn_stub

_N = [{"name": "n", "type": "int"}]


def _wrap(**kw):
    return R._py_wrapper_for_function(
        "fb", _N, kw.pop("return_type", "size_t"), c_name="fb", **kw
    )


# -- codegen (no compiler) ---------------------------------------------------


def test_ndarray_count_zero_raises_and_releases_the_output():
    src = _wrap(
        variable_output=True,
        out_type="uint8_t",
        out_size="n",
        check_return=True,
    )
    i_call = src.index("size_t _n = (size_t)fb(")
    i_check = src.index("if (_n == 0) {", i_call)
    i_trim = src.index("PyArray_DIMS((PyArrayObject *)_out)[0]", i_call)
    assert i_call < i_check < i_trim
    block = src[i_check:i_trim]
    assert "Py_DECREF(_out);" in block
    assert '"fb failed (returned 0)"' in block
    assert "return NULL;" in block


def test_str_count_zero_raises_and_frees_the_buffer():
    src = _wrap(
        variable_output=True, out_type="str", out_size="n", check_return=True
    )
    i_check = src.index("if (_n == 0) {")
    assert "free(_buf);" in src[i_check : src.index("return NULL;", i_check)]
    assert '"fb failed (returned 0)"' in src


def test_without_the_key_nothing_changes():
    src = _wrap(variable_output=True, out_type="uint8_t", out_size="n")
    assert "if (_n == 0)" not in src


def test_void_variable_output_refuses_the_key():
    with pytest.raises(ValueError, match="never reads the C return"):
        _wrap(
            return_type="void",
            variable_output=True,
            out_type="uint8_t",
            out_size="n",
            check_return=True,
        )


def test_caller_sized_out_type_refuses_the_key():
    with pytest.raises(ValueError, match="never reads the C return"):
        R._py_wrapper_for_function(
            "fb",
            [{"name": "x", "type": "double[]"}],
            "int",
            out_type="double",
            check_return=True,
            c_name="fb",
        )


def test_stub_keeps_the_output_type():
    stub = _fn_stub(
        {
            "name": "fb",
            "return_type": "size_t",
            "variable_output": True,
            "out_type": "uint8_t",
            "out_size": "n",
            "check_return": True,
            "params": _N,
        }
    )
    assert "-> None" not in stub
    assert "NDArray" in stub


def test_status_form_is_unchanged():
    # gh-363's shape still returns None and raises on a non-zero rc.
    stub = _fn_stub({"name": "c", "return_type": "int", "check_return": True})
    assert "def c() -> None:" in stub


# -- compile + run -----------------------------------------------------------

_CC = default_cc()

_BACKING = """
#include <stddef.h>
#include <stdint.h>
#include <string.h>
/* Writes n bits and returns n; refuses (returns 0) for n <= 0. */
size_t fb(int n, uint8_t *out) {
    if (n <= 0) return 0;
    for (int i = 0; i < n; i++) out[i] = (uint8_t)(i & 1);
    return (size_t)n;
}
size_t fs(int n, char *out) {
    if (n <= 0) return 0;
    memset(out, 'x', (size_t)n);
    return (size_t)n;
}
"""

_FNS = [
    {
        "name": "fb",
        "return_type": "size_t",
        "variable_output": True,
        "out_type": "uint8_t",
        "out_size": "n > 0 ? n : 1",
        "check_return": True,
        "params": _N,
    },
    {
        "name": "fs",
        "return_type": "size_t",
        "variable_output": True,
        "out_type": "str",
        "out_size": "n > 0 ? n : 1",
        "check_return": True,
        "params": _N,
    },
]


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_compiles_and_raises_on_zero(tmp_path):
    w = make_functions_ctx(
        "vom", "Vom", _FNS, {}, owner={"project": {"name": "p"}}
    )
    src = f"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
{_BACKING}
{w["function_wrappers"]}
{w["module_methods_def"]}
static struct PyModuleDef _md = {{
    PyModuleDef_HEAD_INIT, "vom", NULL, -1, {w["module_m_methods"]},
    NULL, NULL, NULL, NULL
}};
PyMODINIT_FUNC PyInit_vom(void) {{
    import_array();
    return PyModule_Create(&_md);
}}
"""
    c = tmp_path / "vom.c"
    c.write_text(src)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    so = tmp_path / f"vom{suffix}"
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
    spec = importlib.util.spec_from_file_location("vom", so)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.fb(4).tolist() == [0, 1, 0, 1]
    assert mod.fs(3) == "xxx"
    with pytest.raises(RuntimeError, match="fb failed"):
        mod.fb(0)
    with pytest.raises(RuntimeError, match="fs failed"):
        mod.fs(-1)
