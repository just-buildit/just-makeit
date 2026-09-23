"""gh-1490: a module function frees its array only AFTER the C call reads it.

A scalar-returning module function with an array parameter rendered its
cleanup first and its call second::

    flags_arr = PyArray_FROM_OTF (flags_obj, NPY_UINT8, ...);
    flags     = PyArray_DATA (flags_arr);
    Py_DECREF (flags_arr);
    return PyLong_FromLong (count_set (flags, ...));

When the caller's array already IS a C-contiguous uint8 array, FROM_OTF hands
back a new reference to it and the early DECREF is harmless -- which is why it
survived. When it is not (a ``bool`` array, a float array, a strided view),
FROM_OTF returns a TEMPORARY holding the only reference, the DECREF frees it,
and the call reads freed memory. Downstream (doppler-dsp/doppler#1477) that was
an access violation on Windows one run in three on seeded inputs, and a silent
wrong answer on Linux.

The text test pins the order on every platform. The build test proves the
behaviour: glibc's ``malloc.perturb`` tunable fills FREED memory with a fixed
nonzero byte, so a kernel that counts set flags reads every element as set
when it reads the freed temporary -- deterministic, not a heap-layout lottery.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._render import make_functions_ctx

_FNS = [
    {
        "name": "count_set",
        "return_type": "size_t",
        "params": [{"name": "flags", "type": "uint8_t[]"}],
    },
    {
        "name": "twice",
        "return_type": "int",
        "params": [{"name": "v", "type": "int"}],
    },
]


def _wrappers() -> str:
    return make_functions_ctx("cnt", "Cnt", _FNS, {})["function_wrappers"]


def _body(w: str, fn: str) -> str:
    """The rendered wrapper of one function."""
    start = w.index(f"_bind_{fn}(")
    end = w.find("\nstatic PyObject *", start)
    return w[start : end if end > 0 else len(w)]


def test_array_is_released_after_the_call_reads_it():
    """The call precedes the DECREF, and the return converts the captured
    result -- never a call nested inside a return that follows the free."""
    body = _body(_wrappers(), "count_set")
    call = body.index("_r = count_set(")
    free = body.index("Py_DECREF(flags_arr)", call)
    ret = body.index("return ", free)
    assert call < free < ret, body
    # No free of the array anywhere before the call reads it.
    assert "Py_DECREF(flags_arr)" not in body[:call], body
    assert (
        "return PyLong_FromUnsignedLongLong((unsigned long long)_r);" in body
    )


def test_a_scalar_only_function_keeps_its_one_line_form():
    """No cleanup, nothing to order: the output is unchanged by the fix."""
    body = _body(_wrappers(), "twice")
    assert "_r =" not in body
    assert "    return PyLong_FromLong((long)twice(v));" in body, body


_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_GLIBC = platform.libc_ver()[0] == "glibc"

_BACKING = """
#include <stddef.h>
#include <stdint.h>
size_t count_set(const uint8_t *flags, size_t flags_len) {
    size_t n = 0;
    for (size_t i = 0; i < flags_len; i++) n += flags[i] ? 1u : 0u;
    return n;
}
int twice(int v) { return 2 * v; }
"""


def _build(tmp: Path) -> Path:
    w = make_functions_ctx("cnt", "Cnt", _FNS, {})
    src = f"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
{_BACKING}
{w["function_wrappers"]}
{w["module_methods_def"]}
static struct PyModuleDef _md = {{
    PyModuleDef_HEAD_INIT, "cnt", NULL, -1, {w["module_m_methods"]},
    NULL, NULL, NULL, NULL
}};
PyMODINIT_FUNC PyInit_cnt(void) {{
    import_array();
    return PyModule_Create(&_md);
}}
"""
    c = tmp / "cnt.c"
    c.write_text(src)
    so = tmp / f"cnt{sysconfig.get_config_var('EXT_SUFFIX') or '.so'}"
    subprocess.run(
        [
            _CC,
            "-shared",
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
    return so


_PROBE = """
import importlib.util, sys
import numpy as np
spec = importlib.util.spec_from_file_location("cnt", sys.argv[1])
cnt = importlib.util.module_from_spec(spec); spec.loader.exec_module(cnt)
flags = np.zeros(50_000, dtype=bool); flags[:10] = True
print(cnt.count_set(flags), cnt.count_set(flags.astype(np.uint8)))
"""


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
@pytest.mark.skipif(not _GLIBC, reason="needs glibc's malloc.perturb")
def test_a_cast_array_is_read_before_it_is_freed(tmp_path):
    """A bool array is CAST to uint8 -- a temporary the wrapper owns. Under
    perturb, freed memory reads as a nonzero byte, so a read after the free
    counts all 50 000 elements; a read before it counts the 10 set."""
    so = _build(tmp_path)
    env = {
        **os.environ,
        "GLIBC_TUNABLES": "glibc.malloc.perturb=165",
        "PYTHONMALLOC": "malloc",
    }
    r = subprocess.run(
        [sys.executable, "-c", _PROBE, str(so)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    cast, direct = r.stdout.split()
    assert direct == "10", r.stdout
    assert cast == "10", (
        f"bool input counted {cast} of 50000 set, uint8 counted {direct}: "
        "the wrapper read the cast temporary after freeing it"
    )
