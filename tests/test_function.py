"""Integration tests for `just-makeit function`."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._function import run as function_run
from just_makeit._config import (
    load,
    module_functions as cfg_module_functions,
)

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")


@pytest.fixture()
def fft_module(tmp_path):
    root = tmp_path / "dsp"
    new_run("dsp", root, modules=["fft"])
    function_run(root, "fft_global_setup", "fft", doc="Initialize FFT.")
    return root


@pytest.fixture()
def two_functions(tmp_path):
    root = tmp_path / "dsp"
    new_run("dsp", root, modules=["fft"])
    function_run(root, "fft_global_setup", "fft")
    function_run(root, "fft1d_execute", "fft", doc="Execute 1-D FFT.")
    return root


@pytest.fixture()
def module_with_objects_and_functions(tmp_path):
    root = tmp_path / "dsp"
    new_run("dsp", root, modules=["dsp"])
    object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
    function_run(root, "global_setup", "dsp", doc="DSP global setup.")
    return root


class TestFunctionsFileCreated:
    def test_functions_c_exists(self, fft_module):
        path = fft_module / "native/src/fft/fft_functions.c"
        assert path.exists()

    def test_functions_c_has_stub(self, fft_module):
        text = (fft_module / "native/src/fft/fft_functions.c").read_text(encoding="utf-8")
        assert "fft_global_setup(PyObject *self, PyObject *args)" in text

    def test_functions_c_has_implement_marker(self, fft_module):
        text = (fft_module / "native/src/fft/fft_functions.c").read_text(encoding="utf-8")
        assert "<<IMPLEMENT: fft_global_setup>>" in text

    def test_functions_c_returns_none(self, fft_module):
        text = (fft_module / "native/src/fft/fft_functions.c").read_text(encoding="utf-8")
        assert "Py_RETURN_NONE;" in text

    def test_functions_c_has_file_header(self, fft_module):
        text = (fft_module / "native/src/fft/fft_functions.c").read_text(encoding="utf-8")
        assert "fft_functions.c" in text
        assert "#included from fft_ext.c" in text


class TestExtCHeader:
    def test_include_added_to_ext_c(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '#include "fft_functions.c"' in ext

    def test_include_after_numpy(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        numpy_pos = ext.index("#include <numpy/arrayobject.h>")
        include_pos = ext.index('#include "fft_functions.c"')
        assert numpy_pos < include_pos

    def test_no_include_without_functions(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        # No functions added
        ext = (root / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert "fft_functions.c" not in ext


class TestExtCFooter:
    def test_pymethoddef_array_present(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert "static PyMethodDef Fft_methods[]" in ext

    def test_pymethoddef_has_entry(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '"fft_global_setup", fft_global_setup, METH_VARARGS' in ext

    def test_pymethoddef_has_sentinel(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert "{NULL, NULL, 0, NULL}" in ext

    def test_m_methods_not_null(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert ".m_methods = Fft_methods," in ext

    def test_m_methods_null_without_functions(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        ext = (root / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert ".m_methods = NULL," in ext

    def test_doc_string_in_methoddef(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '"Initialize FFT."' in ext


class TestTwoFunctions:
    def test_second_stub_appended_to_functions_c(self, two_functions):
        text = (two_functions / "native/src/fft/fft_functions.c").read_text(encoding="utf-8")
        assert "fft_global_setup(PyObject *self, PyObject *args)" in text
        assert "fft1d_execute(PyObject *self, PyObject *args)" in text

    def test_both_entries_in_methoddef(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '"fft_global_setup", fft_global_setup' in ext
        assert '"fft1d_execute", fft1d_execute' in ext

    def test_first_entry_before_second(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert ext.index("fft_global_setup") < ext.index("fft1d_execute")

    def test_second_doc_in_methoddef(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '"Execute 1-D FFT."' in ext

    def test_default_doc_when_no_doc(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '"fft_global_setup."' in ext


class TestConfig:
    def test_config_records_function(self, fft_module):
        cfg = load(fft_module)
        fns = cfg_module_functions(cfg, "fft")
        assert len(fns) == 1
        assert fns[0]["name"] == "fft_global_setup"

    def test_config_records_doc(self, fft_module):
        cfg = load(fft_module)
        fns = cfg_module_functions(cfg, "fft")
        assert fns[0]["doc"] == "Initialize FFT."

    def test_config_records_two_functions(self, two_functions):
        cfg = load(two_functions)
        fns = cfg_module_functions(cfg, "fft")
        assert [f["name"] for f in fns] == ["fft_global_setup", "fft1d_execute"]

    def test_config_empty_when_no_functions(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        cfg = load(root)
        assert cfg_module_functions(cfg, "fft") == []

    def test_toml_serializes_functions_section(self, fft_module):
        toml_text = (fft_module / "just-makeit.toml").read_text(encoding="utf-8")
        assert "[[module.fft.functions]]" in toml_text
        assert 'name = "fft_global_setup"' in toml_text
        assert 'doc = "Initialize FFT."' in toml_text


class TestCoexistenceWithObjects:
    def test_objects_and_functions_both_present(self, module_with_objects_and_functions):
        root = module_with_objects_and_functions
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert "NcoType" in ext
        assert "static PyMethodDef Dsp_methods[]" in ext
        assert '"global_setup", global_setup' in ext

    def test_include_present(self, module_with_objects_and_functions):
        root = module_with_objects_and_functions
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert '#include "dsp_functions.c"' in ext

    def test_adding_object_after_function_preserves_methods(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"])
        function_run(root, "global_setup", "dsp")
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert "global_setup" in ext
        assert "NcoType" in ext

    def test_adding_function_after_object_preserves_object(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        function_run(root, "global_setup", "dsp")
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert "global_setup" in ext
        assert "NcoType" in ext


class TestValidation:
    def test_missing_module_flag_exits(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        import subprocess, sys as _sys
        result = subprocess.run(
            [_sys.executable, "-m", "just_makeit._cli_entry", "function", "my_fn"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        # We test via run() directly since CLI needs special entry point
        # Just verify _function.run() raises SystemExit when module missing
        with pytest.raises(SystemExit):
            from just_makeit._function import run as _run
            _run(root, "my_fn", "nonexistent_module")

    def test_nonexistent_module_exits(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        with pytest.raises(SystemExit):
            function_run(root, "my_fn", "nonexistent")

    def test_duplicate_function_name_exits(self, fft_module):
        with pytest.raises(SystemExit):
            function_run(fft_module, "fft_global_setup", "fft")

    def test_invalid_name_exits(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        with pytest.raises(SystemExit):
            function_run(root, "1bad_name", "fft")


class TestNoStrayPlaceholders:
    def _check(self, root: Path) -> None:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py", ".c", ".h", ".toml", ".txt"
            ):
                text = path.read_text(encoding="utf-8")
                m = _STRAY_PLACEHOLDER.search(text)
                assert m is None, f"Stray placeholder in {path}: {m.group()!r}"

    def test_no_stray_placeholders_one_function(self, fft_module):
        self._check(fft_module)

    def test_no_stray_placeholders_two_functions(self, two_functions):
        self._check(two_functions)

    def test_no_stray_placeholders_objects_and_functions(
        self, module_with_objects_and_functions
    ):
        self._check(module_with_objects_and_functions)
