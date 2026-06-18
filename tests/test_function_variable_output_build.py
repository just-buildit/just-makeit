"""End-to-end BUILD test for gh-318 stateless `variable_output` module
functions. Renders the generated bindings, wraps them in a minimal module
around a real C backing, compiles + imports the ``.so``, and calls the
self-sizing functions for real — the first real compile of this shape.

Skipped where no C compiler is available."""

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

from just_makeit._render import make_functions_ctx

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
pytestmark = pytest.mark.skipif(_CC is None, reason="no C compiler available")

_FNS = [
    {
        # scalar args -> array sized by an out_size C *expression* (a sizing fn).
        "name": "rrc_taps",
        "return_type": "void",
        "out_type": "float",
        "variable_output": True,
        "out_size": "rrc_ntaps(sps, span)",
        "params": [
            {"name": "beta", "type": "double"},
            {"name": "sps", "type": "int"},
            {"name": "span", "type": "int"},
        ],
    },
    {
        # array-in -> larger array sized by the input length (`x_len`) * a scalar.
        "name": "upsample",
        "return_type": "void",
        "out_type": "float",
        "variable_output": True,
        "out_size": "x_len * factor",
        "params": [
            {"name": "x", "type": "float[]"},
            {"name": "factor", "type": "int"},
        ],
    },
    {
        # a counting fn: allocate a cap, write `got <= cap`, trim to the return.
        "name": "nonneg",
        "return_type": "size_t",
        "out_type": "float",
        "variable_output": True,
        "out_size": "x_len",
        "params": [{"name": "x", "type": "float[]"}],
    },
]

_BACKING = """
#include <stddef.h>
size_t rrc_ntaps(int sps, int span) { return (size_t)(2 * sps * span + 1); }
void rrc_taps(double beta, int sps, int span, float *out) {
    size_t n = rrc_ntaps(sps, span);
    for (size_t i = 0; i < n; i++) out[i] = (float)beta + (float)i;
}
void upsample(const float *x, size_t n, int factor, float *out) {
    for (size_t i = 0; i < n * (size_t)factor; i++) out[i] = 0.0f;
    for (size_t i = 0; i < n; i++) out[i * (size_t)factor] = x[i];
}
/* keep only the non-negative inputs; returns how many were kept */
size_t nonneg(const float *x, size_t n, float *out) {
    size_t k = 0;
    for (size_t i = 0; i < n; i++) if (x[i] >= 0.0f) out[k++] = x[i];
    return k;
}
"""


def _build(tmp: Path):
    w = make_functions_ctx("wfm", "Wfm", _FNS)
    src = f"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
{_BACKING}
{w["function_wrappers"]}
{w["module_methods_def"]}
static struct PyModuleDef _md = {{
    PyModuleDef_HEAD_INIT, "wfm", NULL, -1, {w["module_m_methods"]},
    NULL, NULL, NULL, NULL
}};
PyMODINIT_FUNC PyInit_wfm(void) {{
    import_array();
    return PyModule_Create(&_md);
}}
"""
    c = tmp / "wfm.c"
    c.write_text(src)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    so = tmp / f"wfm{suffix}"
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
    spec = importlib.util.spec_from_file_location("wfm", so)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_self_sized_output_compiles_and_runs(tmp_path):
    wfm = _build(tmp_path)

    # scalar args, out_size = rrc_ntaps(4, 8) = 2*4*8+1 = 65; void -> full.
    taps = wfm.rrc_taps(0.5, 4, 8)
    assert taps.shape == (65,) and taps.dtype == np.float32
    assert taps[0] == 0.5  # beta + 0
    # keyword passing works too (positional-or-keyword binding).
    assert wfm.rrc_taps(beta=0.0, sps=1, span=1).shape == (3,)

    # array-in, out_size uses the input length: 3 * 4 = 12.
    up = wfm.upsample(np.array([1, 2, 3], dtype=np.float32), 4)
    assert up.shape == (12,)
    assert up.tolist() == [1, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0, 0]

    # size_t return trims the allocation to the actual count.
    kept = wfm.nonneg(np.array([1, -1, 2, -3, 5], dtype=np.float32))
    assert kept.tolist() == [1.0, 2.0, 5.0]
