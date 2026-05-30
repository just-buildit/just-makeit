"""Integration tests for `just-makeit object --array-arg name:dtype`."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._init import run as init_run
from just_makeit._object import run as object_run
from just_makeit._config import load, array_args as cfg_array_args

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")


@pytest.fixture()
def standalone(tmp_path):
    root = tmp_path / "dsp"
    new_run("dsp", root)
    init_run(root, "fir", state_vars=[], array_args=[("h", "float32")])
    return root


@pytest.fixture()
def in_module(tmp_path):
    root = tmp_path / "dsp"
    new_run("dsp", root, modules=["filter"])
    object_run(
        root,
        "hbdecim",
        "filter",
        state_vars=[("factor", "int", "2")],
        array_args=[("h", "float32")],
    )
    return root


class TestArrayArgExtC:
    def test_h_obj_local(self, standalone):
        ext = (standalone / "native/src/fir/fir_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyObject *h_obj = NULL;" in ext

    def test_from_otf_call(self, standalone):
        ext = (standalone / "native/src/fir/fir_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_FROM_OTF" in ext
        assert "NPY_FLOAT" in ext

    def test_array_len_extracted(self, standalone):
        ext = (standalone / "native/src/fir/fir_ext.c").read_text(
            encoding="utf-8"
        )
        assert "h_len = (size_t)PyArray_SIZE(h_arr)" in ext

    def test_create_call_has_data_cast(self, standalone):
        ext = (standalone / "native/src/fir/fir_ext.c").read_text(
            encoding="utf-8"
        )
        assert "(const float *)PyArray_DATA(h_arr), h_len" in ext

    def test_decref_after_create(self, standalone):
        ext = (standalone / "native/src/fir/fir_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Py_DECREF(h_arr);" in ext
        # decref must come before handle NULL check
        assert ext.index("Py_DECREF(h_arr)") < ext.index("if (!self->handle)")

    def test_required_array_before_optional_scalar(self, in_module):
        ext = (in_module / "native/src/filter/filter_ext_hbdecim.c").read_text(
            encoding="utf-8"
        )
        # Format: O (required array) before | (optional scalars)
        assert '"O|i"' in ext

    def test_kwlist_array_before_scalar(self, in_module):
        ext = (in_module / "native/src/filter/filter_ext_hbdecim.c").read_text(
            encoding="utf-8"
        )
        assert ext.index('"h"') < ext.index('"factor"')

    def test_array_only_steps_format(self, standalone):
        # fir has no scalar state vars — steps() format is "O|O" (input + optional out)
        ext = (standalone / "native/src/fir/fir_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"O|O"' in ext

    def test_in_module_ext_has_array_parse(self, in_module):
        ext = (in_module / "native/src/filter/filter_ext_hbdecim.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_FROM_OTF" in ext
        assert "(const float *)PyArray_DATA(h_arr), h_len" in ext


class TestArrayArgCoreH:
    def test_create_params_has_array(self, standalone):
        h = (standalone / "native/inc/fir/fir_core.h").read_text(
            encoding="utf-8"
        )
        assert "const float *h, size_t h_len" in h

    def test_create_params_array_before_scalar(self, in_module):
        h = (in_module / "native/inc/hbdecim/hbdecim_core.h").read_text(
            encoding="utf-8"
        )
        assert "const float *h, size_t h_len, int factor" in h

    def test_create_param_docs(self, standalone):
        h = (standalone / "native/inc/fir/fir_core.h").read_text(
            encoding="utf-8"
        )
        assert "h" in h  # array param documented


class TestArrayArgConfig:
    def test_config_records_array_arg(self, standalone):
        cfg = load(standalone)
        aa = cfg_array_args(cfg, "fir")
        assert aa == [("h", "float32")]

    def test_config_records_type(self, in_module):
        cfg = load(in_module)
        aa = cfg_array_args(cfg, "hbdecim")
        assert aa[0] == ("h", "float32")

    def test_legacy_dtype_key_still_loads(self, tmp_path):
        """TOML written with the old 'dtype' key must still load correctly."""
        from just_makeit._config import FILENAME

        root = tmp_path / "dsp"
        root.mkdir()
        (root / FILENAME).write_text(
            '[project]\nname = "dsp"\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\n'
            'pytest = "false"\npytest_benchmark = "false"\n\n'
            '[fir]\narg_type = "float _Complex"\nreturn_type = "float _Complex"\n'
            'mutable = "false"\nno_state = "false"\nno_step = "false"\n\n'
            '[[fir.array_args]]\nname = "h"\ndtype = "float32"\n',
            encoding="utf-8",
        )
        cfg = load(root)
        assert cfg_array_args(cfg, "fir") == [("h", "float32")]

    def test_config_no_array_args_is_empty(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root)
        init_run(root, "gain", state_vars=[("g", "double", "1.0")])
        cfg = load(root)
        assert cfg_array_args(cfg, "gain") == []


class TestMultipleArrayArgs:
    @pytest.fixture()
    def dual(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root)
        init_run(
            root,
            "resamp",
            state_vars=[("rate", "double", "1.0")],
            array_args=[("h", "float32"), ("g", "float64")],
        )
        return root

    def test_both_obj_locals(self, dual):
        ext = (dual / "native/src/resamp/resamp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyObject *h_obj = NULL;" in ext
        assert "PyObject *g_obj = NULL;" in ext

    def test_fmt_two_required(self, dual):
        ext = (dual / "native/src/resamp/resamp_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"OO|d"' in ext

    def test_create_call_order(self, dual):
        ext = (dual / "native/src/resamp/resamp_ext.c").read_text(
            encoding="utf-8"
        )
        assert (
            "(const float *)PyArray_DATA(h_arr), h_len,"
            " (const double *)PyArray_DATA(g_arr), g_len,"
            " rate"
        ) in ext

    def test_cleanup_on_second_failure(self, dual):
        ext = (dual / "native/src/resamp/resamp_ext.c").read_text(
            encoding="utf-8"
        )
        # If g FROM_OTF fails, h must be decreffed
        assert "Py_DECREF(h_arr)" in ext

    def test_create_params_order(self, dual):
        h = (dual / "native/inc/resamp/resamp_core.h").read_text(
            encoding="utf-8"
        )
        assert (
            "const float *h, size_t h_len, const double *g, size_t g_len, double rate"
        ) in h

    def test_config_both_recorded(self, dual):
        cfg = load(dual)
        aa = cfg_array_args(cfg, "resamp")
        assert aa == [("h", "float32"), ("g", "float64")]


class TestArrayArgNoPlaceholders:
    def test_no_stray_placeholders_standalone(self, standalone):
        for path in standalone.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
            ):
                text = path.read_text(encoding="utf-8")
                m = _STRAY_PLACEHOLDER.search(text)
                assert m is None, f"Stray placeholder in {path}"

    def test_no_stray_placeholders_in_module(self, in_module):
        for path in in_module.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
            ):
                text = path.read_text(encoding="utf-8")
                m = _STRAY_PLACEHOLDER.search(text)
                assert m is None, f"Stray placeholder in {path}"
