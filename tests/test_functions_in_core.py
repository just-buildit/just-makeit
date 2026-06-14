"""gh-247: module functions in one TU (`<module>_core.c`) instead of one .c
per function.

A module flagged ``functions_in_core = true`` keeps all of its
``[[module.X.functions]]`` bodies in the shared ``<module>_core.c`` — so
`static` helpers live once and the module is one translation unit — and its
CMakeLists lists only ``<module>_core.c``. Off by default (one sacred ``.c`` per
function), so existing projects are unchanged.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._function import run as function_run
from just_makeit._apply import run as apply_run


def _skip():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler"
    return None


_SKIP = _skip()


def _two_fn_module(root, in_core):
    new_run("p", root)
    module_run(root, "measure", functions_in_core=in_core)
    function_run(
        root,
        "min_samples",
        "measure",
        params=[("rate", "double")],
        return_type="int32_t",
    )
    function_run(
        root,
        "rec_nfft",
        "measure",
        params=[("n", "int32_t")],
        return_type="int32_t",
    )


def _measure_dir(root):
    return root / "native/src/measure"


class TestGeneration:
    def test_functions_land_in_core_c(self, tmp_path):
        root = tmp_path / "p"
        _two_fn_module(root, in_core=True)
        core = (_measure_dir(root) / "measure_core.c").read_text()
        assert "min_samples(double rate)" in core
        assert "rec_nfft(int32_t n)" in core

    def test_no_per_function_c_files(self, tmp_path):
        root = tmp_path / "p"
        _two_fn_module(root, in_core=True)
        cs = {p.name for p in _measure_dir(root).glob("*.c")}
        assert cs == {"measure_core.c", "measure_ext.c"}

    def test_cmake_lists_only_core(self, tmp_path):
        root = tmp_path / "p"
        _two_fn_module(root, in_core=True)
        cmake = (_measure_dir(root) / "CMakeLists.txt").read_text()
        assert "add_library(measure_core OBJECT measure_core.c)" in cmake

    def test_flag_round_trips_manifest(self, tmp_path):
        root = tmp_path / "p"
        _two_fn_module(root, in_core=True)
        assert C.functions_in_core(C.load(root), "measure") is True

    def test_default_keeps_per_function_files(self, tmp_path):
        # flag off (default) -> one .c per function, unchanged behaviour.
        root = tmp_path / "p"
        _two_fn_module(root, in_core=False)
        cs = {p.name for p in _measure_dir(root).glob("*.c")}
        assert "min_samples.c" in cs and "rec_nfft.c" in cs
        cmake = (_measure_dir(root) / "CMakeLists.txt").read_text()
        assert "min_samples.c rec_nfft.c" in cmake
        assert C.functions_in_core(C.load(root), "measure") is False


class TestApplyRoundTrip:
    def test_apply_does_not_resurrect_per_function_files(self, tmp_path):
        # The path that broke first: apply replays the module via _module.run,
        # which must carry functions_in_core into the temp manifest *before* the
        # function replay, else per-function .c files reappear and double-define.
        root = tmp_path / "p"
        _two_fn_module(root, in_core=True)
        apply_run(root)
        cs = {p.name for p in _measure_dir(root).glob("*.c")}
        assert cs == {"measure_core.c", "measure_ext.c"}
        cmake = (_measure_dir(root) / "CMakeLists.txt").read_text()
        assert "add_library(measure_core OBJECT measure_core.c)" in cmake


def test_functions_in_core_builds_with_shared_static(tmp_path):
    """End-to-end: two functions share one `static` helper in the single TU and
    the module builds (the duplicate-symbol problem #247 set out to remove)."""
    if _SKIP:
        pytest.skip(_SKIP)
    root = tmp_path / "p"
    _two_fn_module(root, in_core=True)
    core = _measure_dir(root) / "measure_core.c"
    text = core.read_text().replace(
        '#include "measure/measure_core.h"',
        '#include "measure/measure_core.h"\n\n'
        "static int32_t _next_pow2(int32_t n)"
        " { int32_t p = 1; while (p < n) p <<= 1; return p; }",
    )
    # both bodies use the shared static helper
    text = text.replace(
        "return (int32_t)0; /* placeholder */",
        "return _next_pow2((int32_t)rate);",
        1,
    ).replace(
        "return (int32_t)0; /* placeholder */",
        "return _next_pow2(n);",
        1,
    )
    core.write_text(text)
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(root / "build")],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stderr
    bld = subprocess.run(
        ["cmake", "--build", str(root / "build")],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, (
        f"functions-in-core build failed (gh-247):\n{bld.stdout}\n{bld.stderr}"
    )
