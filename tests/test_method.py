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
    new_run("dsp", dest, "nco", [("freq", "double", "0.0")])
    return dest


class TestMethodCreatesStubs:
    def test_methods_c_created(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        assert (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).exists()

    def test_methods_c_has_max_out_stub(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert "nco_execute_cf32_max_out" in text

    def test_methods_c_has_execute_stub(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert "nco_execute_cf32(" in text

    def test_methods_c_has_correct_header_include(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert '#include "nco/nco_core.h"' in text

    def test_second_method_appends_to_methods_c(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        method_run(
            project, "nco", "execute_u32", None,
            "void", "uint32_t", True, [],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert "nco_execute_cf32_max_out" in text
        assert "nco_execute_u32_max_out" in text

    def test_fixed_output_stub_no_max_out(self, project):
        method_run(
            project, "nco", "get_phase", None,
            "void", "double", False, [],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert "nco_get_phase_max_out" not in text
        assert "nco_get_phase(" in text

    def test_fixed_output_with_arg(self, project):
        method_run(
            project, "nco", "process", None,
            "float _Complex", "float _Complex", False, [],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert "nco_process(" in text
        # _ctype_display("float _Complex") → "float complex"
        assert "float complex x" in text


class TestMethodUpdatesCMake:
    def test_cmake_has_methods_c(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        cmake = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text()
        assert "nco_methods.c" in cmake

    def test_cmake_object_lib_updated(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        cmake = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text()
        assert (
            "add_library(nco_core OBJECT nco_core.c nco_methods.c)" in cmake
        )

    def test_cmake_not_duplicated_on_second_method(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        method_run(
            project, "nco", "execute_u32", None,
            "void", "uint32_t", True, [],
        )
        cmake = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text()
        assert cmake.count("nco_methods.c") == 1


class TestMethodUpdatesExtC:
    def test_ext_c_has_buf_field(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "_execute_cf32_buf" in ext

    def test_ext_c_has_malloc_alloc(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "nco_execute_cf32_max_out" in ext
        assert "malloc(" in ext

    def test_ext_c_has_free_in_dealloc(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "free(self->_execute_cf32_buf)" in ext

    def test_ext_c_has_zero_copy_wrapper(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "PyArray_SimpleNewFromData" in ext
        assert "PyArray_SetBaseObject" in ext

    def test_ext_c_has_pymethoddef_entry(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert '"execute_cf32"' in ext

    def test_ext_c_fixed_output_scalar_return(self, project):
        method_run(
            project, "nco", "get_phase", None,
            "void", "double", False, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "nco_get_phase(self->handle)" in ext
        assert "PyArray_SimpleNewFromData" not in ext

    def test_ext_c_fixed_output_noargs_flag(self, project):
        method_run(
            project, "nco", "get_phase", None,
            "void", "double", False, [],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "METH_NOARGS" in ext

    def test_core_h_has_method_decl(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        h = (
            project / "native" / "inc" / "nco" / "nco_core.h"
        ).read_text()
        assert "nco_execute_cf32_max_out" in h
        assert "nco_execute_cf32(" in h


class TestMethodMultiOutput:
    def test_multi_output_buf_fields(self, project):
        method_run(
            project, "nco", "execute_iq", None,
            "void", "float _Complex", True, ["float _Complex"],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "_execute_iq_buf" in ext
        assert "_execute_iq_buf_1" in ext

    def test_multi_output_tuple_pack(self, project):
        method_run(
            project, "nco", "execute_iq", None,
            "void", "float _Complex", True, ["float _Complex"],
        )
        ext = (
            project / "native" / "src" / "nco" / "nco_ext.c"
        ).read_text()
        assert "PyTuple_Pack" in ext

    def test_multi_output_stubs_in_methods_c(self, project):
        method_run(
            project, "nco", "execute_iq", None,
            "void", "float _Complex", True, ["float _Complex"],
        )
        text = (
            project / "native" / "src" / "nco" / "nco_methods.c"
        ).read_text()
        assert "nco_execute_iq_max_out" in text
        assert "nco_execute_iq(" in text


class TestMethodUpdatesConfig:
    def test_config_has_method(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        cfg = load(project)
        names = [m["name"] for m in methods(cfg, "nco")]
        assert "execute_cf32" in names

    def test_config_records_variable_output(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "execute_cf32")
        assert m.get("variable_output") is True

    def test_config_records_types(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "execute_cf32")
        assert m["arg_type"] == "void"
        assert m["return_type"] == "float _Complex"

    def test_config_fixed_output_no_variable_flag(self, project):
        method_run(
            project, "nco", "get_phase", None,
            "void", "double", False, [],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "get_phase")
        assert not m.get("variable_output", False)

    def test_config_multi_output_recorded(self, project):
        method_run(
            project, "nco", "execute_iq", None,
            "void", "float _Complex", True, ["float _Complex"],
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "execute_iq")
        assert m.get("multi_output") == ["float _Complex"]


class TestMethodValidation:
    def test_no_config_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            method_run(
                tmp_path, "nco", "execute_cf32", None,
                "void", "float _Complex", True, [],
            )

    def test_unknown_object_exits(self, project):
        with pytest.raises(SystemExit):
            method_run(
                project, "nonexistent", "execute_cf32", None,
                "void", "float _Complex", True, [],
            )

    def test_duplicate_method_name_exits(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        with pytest.raises(SystemExit):
            method_run(
                project, "nco", "execute_cf32", None,
                "void", "float _Complex", True, [],
            )


def _check_no_placeholders(project: Path) -> None:
    """Assert no unreplaced <<placeholder>> tokens in generated files.

    <<IMPLEMENT:...>> guidance markers in stubs are intentional and excluded.
    """
    for path in project.rglob("*"):
        if path.is_file() and path.suffix in (
            ".py", ".c", ".h", ".toml", ".txt"
        ):
            text = path.read_text(encoding="utf-8")
            m = _STRAY_PLACEHOLDER.search(text)
            assert m is None, f"Unreplaced placeholder in {path}"


class TestMethodNoUnreplacedPlaceholders:
    def test_no_placeholders_variable_output(self, project):
        method_run(
            project, "nco", "execute_cf32", None,
            "void", "float _Complex", True, [],
        )
        _check_no_placeholders(project)

    def test_no_placeholders_fixed_output(self, project):
        method_run(
            project, "nco", "get_phase", None,
            "void", "double", False, [],
        )
        _check_no_placeholders(project)

    def test_no_placeholders_multi_output(self, project):
        method_run(
            project, "nco", "execute_iq", None,
            "void", "float _Complex", True, ["float _Complex"],
        )
        _check_no_placeholders(project)
