"""Integration tests for `just-makeit method`."""

import re
import sys
from pathlib import Path

import pytest

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._method import run as method_run
from just_makeit._config import load, methods


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["nco"], [("freq", "double", "0.0")])
    return dest


class TestMethodCreatesStubs:
    def test_core_c_has_stub_appended(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_cf32" in text

    def test_methods_c_has_max_out_stub(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_cf32_max_out" in text

    def test_methods_c_has_execute_stub(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_cf32(" in text

    def test_core_c_has_include(self, project):
        # nco_core.c must already contain its own header include before any
        # method stub is appended.
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_core.h" in text

    def test_second_method_appends_to_methods_c(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        method_run(
            project,
            "nco",
            "execute_u32",
            None,
            "void",
            "uint32_t",
            True,
            [],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_cf32_max_out" in text
        assert "nco_execute_u32_max_out" in text

    def test_fixed_output_stub_no_max_out(self, project):
        method_run(
            project,
            "nco",
            "get_phase",
            None,
            "void",
            "double",
            False,
            [],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_get_phase_max_out" not in text
        assert "nco_get_phase(" in text

    def test_fixed_output_with_arg(self, project):
        method_run(
            project,
            "nco",
            "process",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_process(" in text
        # _ctype_display("float _Complex") → "float complex"
        assert "float complex x" in text


class TestMethodDoesNotModifyCMake:
    """Method stubs go into _core.c; CMakeLists.txt must NOT be touched."""

    def test_cmake_has_no_methods_c(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        cmake = (project / "native" / "src" / "nco" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "nco_methods.c" not in cmake

    def test_cmake_still_has_single_source(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        cmake = (project / "native" / "src" / "nco" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "add_library(nco_core OBJECT nco_core.c)" in cmake

    def test_cmake_unchanged_after_two_methods(self, project):
        cmake_before = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        method_run(
            project,
            "nco",
            "execute_u32",
            None,
            "void",
            "uint32_t",
            True,
            [],
        )
        cmake_after = (project / "native" / "src" / "nco" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert cmake_before == cmake_after


class TestMethodUpdatesExtC:
    def test_ext_c_has_buf_field(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_execute_cf32_buf" in ext

    def test_ext_c_has_malloc_alloc(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_cf32_max_out" in ext
        assert "malloc(" in ext

    def test_ext_c_has_free_in_dealloc(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "free(self->_execute_cf32_buf)" in ext

    def test_ext_c_has_zero_copy_wrapper(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_SimpleNewFromData" in ext
        assert "PyArray_SetBaseObject" in ext

    def test_ext_c_has_pymethoddef_entry(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"execute_cf32"' in ext

    def test_ext_c_fixed_output_scalar_return(self, project):
        method_run(
            project,
            "nco",
            "get_phase",
            None,
            "void",
            "double",
            False,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "nco_get_phase(self->handle)" in ext
        assert "PyArray_SimpleNewFromData" not in ext

    def test_ext_c_fixed_output_noargs_flag(self, project):
        method_run(
            project,
            "nco",
            "get_phase",
            None,
            "void",
            "double",
            False,
            [],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "METH_NOARGS" in ext

    def test_core_h_has_method_decl(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        h = (project / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_cf32_max_out" in h
        assert "nco_execute_cf32(" in h


class TestMethodMultiOutput:
    def test_multi_output_buf_fields(self, project):
        method_run(
            project,
            "nco",
            "execute_iq",
            None,
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_execute_iq_buf" in ext
        assert "_execute_iq_buf_1" in ext

    def test_multi_output_tuple_pack(self, project):
        method_run(
            project,
            "nco",
            "execute_iq",
            None,
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyTuple_Pack" in ext

    def test_multi_output_stubs_in_core_c(self, project):
        method_run(
            project,
            "nco",
            "execute_iq",
            None,
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "nco_execute_iq_max_out" in text
        assert "nco_execute_iq(" in text


class TestMethodUpdatesConfig:
    def test_config_has_method(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        cfg = load(project)
        names = [m["name"] for m in methods(cfg, "nco")]
        assert "execute_cf32" in names

    def test_config_records_variable_output(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "execute_cf32")
        assert m.get("variable_output") is True

    def test_config_records_types(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "execute_cf32")
        assert m["arg_type"] == "void"
        assert m["return_type"] == "float _Complex"

    def test_config_fixed_output_no_variable_flag(self, project):
        method_run(
            project,
            "nco",
            "get_phase",
            None,
            "void",
            "double",
            False,
            [],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "get_phase")
        assert not m.get("variable_output", False)

    def test_config_multi_output_recorded(self, project):
        method_run(
            project,
            "nco",
            "execute_iq",
            None,
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "execute_iq")
        assert m.get("multi_output") == ["float _Complex"]


class TestMethodValidation:
    def test_no_config_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            method_run(
                tmp_path,
                "nco",
                "execute_cf32",
                None,
                "void",
                "float _Complex",
                True,
                [],
            )

    def test_unknown_object_exits(self, project):
        with pytest.raises(SystemExit):
            method_run(
                project,
                "nonexistent",
                "execute_cf32",
                None,
                "void",
                "float _Complex",
                True,
                [],
            )

    def test_duplicate_method_name_exits(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        with pytest.raises(SystemExit):
            method_run(
                project,
                "nco",
                "execute_cf32",
                None,
                "void",
                "float _Complex",
                True,
                [],
            )


def _check_no_placeholders(project: Path) -> None:
    """Assert no unreplaced <<placeholder>> tokens in generated files.

    <<IMPLEMENT:...>> guidance markers in stubs are intentional and excluded.
    """
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
            text = path.read_text(encoding="utf-8")
            m = _STRAY_PLACEHOLDER.search(text)
            assert m is None, f"Unreplaced placeholder in {path}"


class TestMethodNoUnreplacedPlaceholders:
    def test_no_placeholders_variable_output(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        _check_no_placeholders(project)

    def test_no_placeholders_fixed_output(self, project):
        method_run(
            project,
            "nco",
            "get_phase",
            None,
            "void",
            "double",
            False,
            [],
        )
        _check_no_placeholders(project)

    def test_no_placeholders_multi_output(self, project):
        method_run(
            project,
            "nco",
            "execute_iq",
            None,
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        _check_no_placeholders(project)

    def test_no_placeholders_fixed_multi_output(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        _check_no_placeholders(project)


class TestMethodFixedMultiOutput:
    """Fixed-output --multi-output: out-pointer params in C, tuple in Python."""

    def test_c_stub_has_out_pointer_param(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "uint8_t *out1" in text

    def test_c_stub_suppresses_out_pointer(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "(void)out1;" in text

    def test_decl_has_out_pointer_param(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        h = (project / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "uint8_t *out1" in h

    def test_ext_c_has_tuple_pack(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyTuple_Pack" in ext

    def test_ext_c_stack_alloc_out(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "out1 = 0U" in ext

    def test_ext_c_passes_addr_to_c(self, project):
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "&out1" in ext

    def test_ext_c_no_array_buf(self, project):
        """Fixed multi-output must NOT allocate a pre-allocated buffer."""
        method_run(
            project,
            "nco",
            "step_ovf",
            None,
            "float",
            "float",
            False,
            ["uint8_t"],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_step_ovf_buf" not in ext

    def test_noarg_fixed_multi_output(self, project):
        """No-arg fixed method with multi-output still gets out-pointer."""
        method_run(
            project,
            "nco",
            "tick_ovf",
            None,
            "void",
            "float",
            False,
            ["uint8_t"],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "uint8_t *out1" in text
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyTuple_Pack" in ext

    def test_multiple_extra_outputs(self, project):
        method_run(
            project,
            "nco",
            "step_multi",
            None,
            "float",
            "float",
            False,
            ["uint8_t", "uint32_t"],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "uint8_t *out1" in text
        assert "uint32_t *out2" in text
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyTuple_Pack(3," in ext


class TestMethodWithParams:
    """--param name:type generates named C params and typed Python wrapper."""

    def test_c_stub_has_named_params(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "float freq" in text
        assert "int32_t mode" in text

    def test_c_stub_suppresses_named_params(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "(void)freq;" in text
        assert "(void)mode;" in text

    def test_c_stub_no_return_for_void(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float")],
        )
        text = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "return (void)" not in text

    def test_decl_has_named_params(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        h = (project / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "float freq" in h
        assert "int32_t mode" in h

    def test_ext_c_parse_tuple_format(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        # float -> "f", int32_t -> "l" (long intermediate)
        assert '"fl"' in ext

    def test_ext_c_meth_varargs(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float")],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "METH_VARARGS" in ext

    def test_ext_c_calls_c_function_with_params(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "nco_configure(self->handle, freq, mode)" in ext

    def test_ext_c_scalar_return(self, project):
        method_run(
            project,
            "nco",
            "get_snr",
            None,
            "void",
            "float",
            False,
            [],
            params=[("window", "int32_t")],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyFloat_FromDouble" in ext
        assert "nco_get_snr(self->handle, window)" in ext

    def test_ext_c_complex_param_uses_raw_var(self, project):
        method_run(
            project,
            "nco",
            "mix",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("lo", "float _Complex")],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "lo_raw" in ext
        assert '"D"' in ext

    def test_config_stores_params(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "configure")
        assert m.get("params") == [
            {"name": "freq", "type": "float"},
            {"name": "mode", "type": "int32_t"},
        ]

    def test_no_placeholders_with_params(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "void",
            "void",
            False,
            [],
            params=[("freq", "float"), ("mode", "int32_t")],
        )
        _check_no_placeholders(project)


class TestMethodWithArrayParam:
    """--param name:type[] generates numpy array parse + const ptr/len signature."""

    @pytest.fixture()
    def arr_method(self, project):
        method_run(
            project,
            "nco",
            "process",
            None,
            "void",
            "void",
            False,
            [],
            params=[("ctrl", "float _Complex[]")],
        )
        return project

    @pytest.fixture()
    def mixed_method(self, project):
        method_run(
            project,
            "nco",
            "process_mixed",
            None,
            "void",
            "void",
            False,
            [],
            params=[("gain", "float"), ("buf", "float[]")],
        )
        return project

    def test_c_stub_has_const_ptr_param(self, arr_method):
        text = (arr_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "const float complex *ctrl" in text

    def test_c_stub_has_len_param(self, arr_method):
        text = (arr_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "size_t ctrl_len" in text

    def test_c_stub_suppresses_ptr_and_len(self, arr_method):
        text = (arr_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "(void)ctrl;" in text
        assert "(void)ctrl_len;" in text

    def test_ext_c_has_pyarray_from_otf(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "PyArray_FROM_OTF" in ext
        assert "NPY_COMPLEX64" in ext

    def test_ext_c_format_has_O(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert '"O"' in ext

    def test_ext_c_passes_ptr_and_len(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "ctrl_len" in ext

    def test_ext_c_has_decref(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "Py_DECREF(ctrl_arr)" in ext

    def test_mixed_params_scalar_and_array(self, mixed_method):
        text = (mixed_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "float gain" in text
        assert "const float *buf" in text
        assert "size_t buf_len" in text

    def test_mixed_format_string(self, mixed_method):
        ext = (mixed_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert '"fO"' in ext

    def test_config_stores_array_type(self, arr_method):
        cfg = load(arr_method)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "process")
        assert m.get("params") == [{"name": "ctrl", "type": "float _Complex[]"}]

    def test_no_placeholders(self, arr_method):
        _check_no_placeholders(arr_method)


class TestMethodArrayArgNoParams:
    """Bug fix: array arg_type (not --param) must emit PyArray_FROM_OTF, not
    'float[] x;' invalid C syntax."""

    @pytest.fixture()
    def add_method(self, project):
        method_run(
            project,
            "nco",
            "add",
            None,
            "float[]",
            "void",
            False,
            [],
        )
        return project

    def test_ext_c_has_pyarray_from_otf(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "PyArray_FROM_OTF" in ext

    def test_ext_c_no_invalid_array_decl(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "float[] x" not in ext

    def test_ext_c_passes_ptr_and_len(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "x_len" in ext

    def test_ext_c_has_decref(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "Py_DECREF(x_arr)" in ext

    def test_no_placeholders(self, add_method):
        _check_no_placeholders(add_method)


class TestMethodArrayArgWithParams:
    """Bug fix: when arg_type is an array AND --param is present, primary arg x
    must not disappear from the parse block or C call."""

    @pytest.fixture()
    def madd_method(self, project):
        method_run(
            project,
            "nco",
            "madd",
            None,
            "float[]",
            "void",
            False,
            [],
            params=[("h", "float[]")],
        )
        return project

    def test_ext_c_parses_both_args(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "x_arr" in ext
        assert "h_arr" in ext

    def test_ext_c_passes_x_to_c(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "x," in ext or "x_len" in ext

    def test_ext_c_passes_h_to_c(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "h_len" in ext

    def test_ext_c_format_has_two_O(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert '"OO"' in ext

    def test_ext_c_decrefs_both(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(encoding="utf-8")
        assert "Py_DECREF(x_arr)" in ext
        assert "Py_DECREF(h_arr)" in ext

    def test_no_placeholders(self, madd_method):
        _check_no_placeholders(madd_method)

    def test_core_c_stub_has_x_ptr(self, madd_method):
        core = (madd_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "const float *x" in core

    def test_core_c_stub_has_x_len(self, madd_method):
        core = (madd_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "size_t x_len" in core

    def test_core_c_stub_has_h_ptr(self, madd_method):
        core = (madd_method / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "const float *h" in core

    def test_core_h_prototype_has_x_and_h(self, madd_method):
        hdr = (madd_method / "native/inc/nco/nco_core.h").read_text(encoding="utf-8")
        assert "const float *x" in hdr
        assert "const float *h" in hdr
