"""End-to-end BUILD test for module-function ``path`` / ``enum`` args (gh-353).

Renders the generated bindings for a function taking a ``path`` arg, an ``enum``
arg, and a plain scalar, wraps them around a tiny hand-C backing, compiles +
imports the ``.so``, and calls them for real: a ``pathlib.Path`` and an enum
choice string must work, and a bad enum string must raise ``ValueError``. This
is the first real compile of the gh-353 shape (the path borrow's gh-219 lifetime
and the _enum_index validation), mirroring ``test_function_variable_output_build``
and ``test_handle_build``. Skipped where no C compiler is available."""

from __future__ import annotations

import importlib.util
import pathlib
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

# A function taking a path (filename), an enum (kind), and a scalar (extra). It
# returns a code computed from the basename length, the enum index, and extra —
# so the test can prove each arg crossed correctly. `kind_index` round-trips the
# validated enum int back out.
_ENUMS = {"log_kind": ["raw", "json", "csv"]}

_FNS = [
    {
        "name": "make_code",
        "return_type": "size_t",
        "params": [
            {"name": "name", "type": "path"},
            {"name": "kind", "type": "int", "enum": "log_kind"},
            {"name": "extra", "type": "int", "default": "100"},
        ],
    },
]

_BACKING = """
#include <stddef.h>
#include <string.h>
/* code = 1000*len(basename-ish) + 10*kind + extra; proves all three args
 * crossed (path as a real C string, enum as its validated int, scalar). */
size_t make_code(const char *name, int kind, int extra) {
    size_t n = name ? strlen(name) : 0;
    return 1000 * n + 10 * (size_t)kind + (size_t)extra;
}
"""


def _build(tmp: Path):
    w = make_functions_ctx("logm", "Logm", _FNS, _ENUMS)
    src = f"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <string.h>
{_BACKING}
{w["function_enum_tables"]}
{w["function_wrappers"]}
{w["module_methods_def"]}
static struct PyModuleDef _md = {{
    PyModuleDef_HEAD_INIT, "logm", NULL, -1, {w["module_m_methods"]},
    NULL, NULL, NULL, NULL
}};
PyMODINIT_FUNC PyInit_logm(void) {{
    import_array();
    return PyModule_Create(&_md);
}}
"""
    c = tmp / "logm.c"
    c.write_text(src)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    so = tmp / f"logm{suffix}"
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
    spec = importlib.util.spec_from_file_location("logm", so)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_path_and_enum_function_compiles_and_runs(tmp_path):
    logm = _build(tmp_path)

    # A pathlib.Path crosses as a C string; "json" validates to enum index 1;
    # extra defaults to 100. code = 1000*len("abcd") + 10*1 + 100 = 4110.
    assert logm.make_code(pathlib.Path("abcd"), "json") == 4110

    # A plain str path works too; "csv" is index 2; explicit extra=5.
    # code = 1000*3 + 10*2 + 5 = 3025.
    assert logm.make_code("xyz", "csv", 5) == 3025

    # Keyword passing (positional-or-keyword binding).
    assert logm.make_code(name="xy", kind="raw", extra=0) == 2000

    # A bad enum choice raises ValueError (and the path borrow is released).
    with pytest.raises(ValueError):
        logm.make_code(pathlib.Path("abcd"), "nope")
