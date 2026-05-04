"""Unit tests for the template rendering system."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._templates import render, make_state_ctx, SUPPORTED_TYPES
from just_makeit._init import _make_ctx


class TestRender:
    def test_single_placeholder(self):
        assert render("hello <<name>>", {"name": "world"}) == "hello world"

    def test_multiple_occurrences(self):
        assert render("<<a>> <<a>>", {"a": "x"}) == "x x"

    def test_unknown_placeholder_unchanged(self):
        assert render("<<unknown>>", {"name": "x"}) == "<<unknown>>"

    def test_c_braces_untouched(self):
        c = "struct { int x; };"
        assert render(c, {"x": "replaced"}) == c

    def test_cmake_dollar_braces_untouched(self):
        cmake = 'set(X "${CMAKE_SOURCE_DIR}/src")'
        assert render(cmake, {"CMAKE_SOURCE_DIR": "replaced"}) == cmake


class TestMakeCtx:
    def test_snake_component(self):
        ctx = _make_ctx("my_filter")
        assert ctx["component"] == "my_filter"

    def test_title_class(self):
        ctx = _make_ctx("my_filter")
        assert ctx["Component"] == "MyFilter"

    def test_upper_macro(self):
        ctx = _make_ctx("my_filter")
        assert ctx["COMPONENT"] == "MY_FILTER"

    def test_project_hyphen(self):
        ctx = _make_ctx("my_filter")
        assert ctx["project"] == "my-filter"

    def test_package_same_as_component(self):
        ctx = _make_ctx("my_filter")
        assert ctx["package"] == "my_filter"

    def test_version_default(self):
        ctx = _make_ctx("my_filter")
        assert ctx["version"] == "0.1.0"

    def test_single_word(self):
        ctx = _make_ctx("gain")
        assert ctx["Component"] == "Gain"
        assert ctx["project"] == "gain"

    def test_three_words(self):
        ctx = _make_ctx("half_band_filter")
        assert ctx["Component"] == "HalfBandFilter"


class TestSupportedTypes:
    def test_double_supported(self):
        assert "double" in SUPPORTED_TYPES

    def test_float_supported(self):
        assert "float" in SUPPORTED_TYPES

    def test_int_supported(self):
        assert "int" in SUPPORTED_TYPES


class TestMakeStateCtx:
    def _ctx(self, state_vars):
        return make_state_ctx("my_filter", "MyFilter", state_vars)

    def test_single_double_struct_field(self):
        ctx = self._ctx([("gain", "double")])
        assert "double gain;" in ctx["state_struct_fields"]

    def test_multi_var_struct_fields(self):
        ctx = self._ctx([("gain", "double"), ("offset", "float")])
        assert "double gain;" in ctx["state_struct_fields"]
        assert "float offset;" in ctx["state_struct_fields"]

    def test_create_params_single(self):
        ctx = self._ctx([("gain", "double")])
        assert ctx["create_params"] == "double gain"

    def test_create_params_multi(self):
        ctx = self._ctx([("gain", "double"), ("n", "int")])
        assert ctx["create_params"] == "double gain, int n"

    def test_getter_setter_decls_contain_component(self):
        ctx = self._ctx([("gain", "double")])
        assert "my_filter_get_gain" in ctx["getter_setter_decls"]
        assert "my_filter_set_gain" in ctx["getter_setter_decls"]

    def test_getter_setter_impls_contain_component(self):
        ctx = self._ctx([("gain", "double")])
        assert "my_filter_get_gain" in ctx["getter_setter_impls"]
        assert "my_filter_set_gain" in ctx["getter_setter_impls"]

    def test_init_kwlist_single(self):
        ctx = self._ctx([("gain", "double")])
        assert ctx["init_kwlist"] == '"gain", NULL'

    def test_init_kwlist_multi(self):
        ctx = self._ctx([("gain", "double"), ("order", "int")])
        assert ctx["init_kwlist"] == '"gain", "order", NULL'

    def test_init_parse_fmt_double(self):
        ctx = self._ctx([("gain", "double")])
        assert ctx["init_parse_fmt"] == "d"

    def test_init_parse_fmt_multi(self):
        ctx = self._ctx([("gain", "double"), ("n", "int")])
        assert ctx["init_parse_fmt"] == "di"

    def test_getter_setter_methods_c_contain_class(self):
        ctx = self._ctx([("gain", "double")])
        assert "MyFilter_get_gain" in ctx["getter_setter_methods_c"]
        assert "MyFilter_set_gain" in ctx["getter_setter_methods_c"]

    def test_getter_setter_pymethoddef(self):
        ctx = self._ctx([("gain", "double")])
        assert '"get_gain"' in ctx["getter_setter_pymethoddef"]
        assert '"set_gain"' in ctx["getter_setter_pymethoddef"]

    def test_init_params_pyi_single(self):
        ctx = self._ctx([("gain", "double")])
        assert ctx["init_params_pyi"] == "gain: float"

    def test_init_params_pyi_multi(self):
        ctx = self._ctx([("gain", "double"), ("n", "int")])
        assert ctx["init_params_pyi"] == "gain: float, n: int"

    def test_getter_setter_stubs_pyi(self):
        ctx = self._ctx([("gain", "double")])
        assert "def get_gain(self) -> float:" in ctx["getter_setter_stubs_pyi"]
        assert "def set_gain(self, value: float) -> None:" in ctx["getter_setter_stubs_pyi"]

    def test_py_create_args_single_double(self):
        ctx = self._ctx([("gain", "double")])
        assert ctx["py_create_args"] == "1.0"

    def test_py_create_args_multi(self):
        ctx = self._ctx([("gain", "double"), ("n", "int")])
        assert ctx["py_create_args"] == "1.0, 1"

    def test_c_create_args_single(self):
        ctx = self._ctx([("gain", "double")])
        assert ctx["c_create_args"] == "1.0"

    def test_c_create_args_float(self):
        ctx = self._ctx([("x", "float")])
        assert ctx["c_create_args"] == "1.0f"

    def test_getter_setter_test_py_contains_class(self):
        ctx = self._ctx([("gain", "double")])
        assert "MyFilter" in ctx["getter_setter_test_py"]
        assert "get_gain" in ctx["getter_setter_test_py"]

    def test_reset_test_py_contains_zero(self):
        ctx = self._ctx([("gain", "double")])
        assert "0.0" in ctx["reset_test_py"]

    def test_invalid_type_raises(self):
        import pytest
        with pytest.raises(ValueError, match="unsupported type"):
            make_state_ctx("comp", "Comp", [("x", "complex128")])
