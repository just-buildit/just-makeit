"""Unit tests for the template rendering system."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._render import render
from just_makeit._context import make_state_ctx, make_methods_ctx
from just_makeit._types import SUPPORTED_TYPES
from just_makeit._init import _make_component_ctx
from just_makeit._new import _make_project_ctx


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


class TestMakeComponentCtx:
    def test_snake_component(self):
        ctx = _make_component_ctx("my_filter")
        assert ctx["component"] == "my_filter"

    def test_title_class(self):
        ctx = _make_component_ctx("my_filter")
        assert ctx["Component"] == "MyFilter"

    def test_upper_macro(self):
        ctx = _make_component_ctx("my_filter")
        assert ctx["COMPONENT"] == "MY_FILTER"

    def test_single_word(self):
        ctx = _make_component_ctx("gain")
        assert ctx["Component"] == "Gain"

    def test_three_words(self):
        ctx = _make_component_ctx("half_band_filter")
        assert ctx["Component"] == "HalfBandFilter"


class TestMakeProjectCtx:
    def test_project_hyphen(self):
        ctx = _make_project_ctx("my_filter")
        assert ctx["project"] == "my-filter"

    def test_package_same_as_project(self):
        ctx = _make_project_ctx("my_filter")
        assert ctx["package"] == "my_filter"

    def test_version_default(self):
        ctx = _make_project_ctx("my_filter")
        assert ctx["version"] == "0.1.0"

    def test_single_word(self):
        ctx = _make_project_ctx("gain")
        assert ctx["project"] == "gain"


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
        ctx = self._ctx([("gain", "double", "0.0")])
        assert "double gain;" in ctx["state_struct_fields"]

    def test_multi_var_struct_fields(self):
        ctx = self._ctx([("gain", "double", "0.0"), ("offset", "float", "0.0f")])
        assert "double gain;" in ctx["state_struct_fields"]
        assert "float offset;" in ctx["state_struct_fields"]

    def test_create_params_single(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert ctx["create_params"] == "double gain"

    def test_create_params_multi(self):
        ctx = self._ctx([("gain", "double", "0.0"), ("n", "int", "0")])
        assert ctx["create_params"] == "double gain, int n"

    def test_getter_setter_decls_contain_component(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert "my_filter_get_gain" in ctx["getter_setter_decls"]
        assert "my_filter_set_gain" in ctx["getter_setter_decls"]

    def test_getter_setter_impls_contain_component(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert "my_filter_get_gain" in ctx["getter_setter_impls"]
        assert "my_filter_set_gain" in ctx["getter_setter_impls"]

    def test_init_kwlist_single(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert ctx["init_kwlist"] == '"gain", NULL'

    def test_init_kwlist_multi(self):
        ctx = self._ctx([("gain", "double", "0.0"), ("order", "int", "0")])
        assert ctx["init_kwlist"] == '"gain", "order", NULL'

    def test_init_parse_fmt_optional(self):
        ctx = self._ctx([("gain", "double", "1.0")])
        assert ctx["init_parse_fmt"] == "|d"

    def test_init_parse_fmt_multi(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("n", "int", "4")])
        assert ctx["init_parse_fmt"] == "|di"

    def test_getter_setter_methods_c_contain_class(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert "MyFilter_get_gain" in ctx["getter_setter_methods_c"]
        assert "MyFilter_set_gain" in ctx["getter_setter_methods_c"]

    def test_getter_setter_pymethoddef(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert '"get_gain"' in ctx["getter_setter_pymethoddef"]
        assert '"set_gain"' in ctx["getter_setter_pymethoddef"]

    def test_init_params_pyi_includes_default(self):
        ctx = self._ctx([("gain", "double", "1.0")])
        assert ctx["init_params_pyi"] == "gain: np.float64 = 1.0"

    def test_init_params_pyi_multi(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("n", "int", "4")])
        assert ctx["init_params_pyi"] == "gain: np.float64 = 1.0, n: np.int32 = 4"

    def test_init_params_pyi_float_strips_suffix(self):
        ctx = self._ctx([("x", "float", "0.5f")])
        assert ctx["init_params_pyi"] == "x: np.float32 = 0.5"

    def test_getter_setter_stubs_pyi(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert "def get_gain(self) -> np.float64:" in ctx["getter_setter_stubs_pyi"]
        assert (
            "def set_gain(self, value: np.float64) -> None:"
            in ctx["getter_setter_stubs_pyi"]
        )

    def test_py_create_args_uses_default(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert ctx["py_create_args"] == "1.5"

    def test_py_create_args_multi(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("n", "int", "4")])
        assert ctx["py_create_args"] == "1.0, 4"

    def test_c_create_args_uses_default(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert ctx["c_create_args"] == "1.5"

    def test_c_create_args_float_literal(self):
        ctx = self._ctx([("x", "float", "0.5f")])
        assert ctx["c_create_args"] == "0.5f"

    def test_reset_assignments_use_default(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert "state->gain = 1.5;" in ctx["reset_assignments"]

    def test_reset_test_py_uses_default(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert "1.5" in ctx["reset_test_py"]

    def test_reset_test_py_dirties_before_reset(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert "set_gain" in ctx["reset_test_py"]
        assert "reset()" in ctx["reset_test_py"]

    def test_getter_setter_test_py_contains_class(self):
        ctx = self._ctx([("gain", "double", "0.0")])
        assert "MyFilter" in ctx["getter_setter_test_py"]
        assert "get_gain" in ctx["getter_setter_test_py"]

    def test_getter_setter_test_uses_default_as_initial(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert "_approx(1.5)" in ctx["getter_setter_test_py"]

    def test_reset_test_c_dirties_before_reset(self):
        ctx = self._ctx([("gain", "double", "1.5")])
        assert "set_gain" in ctx["reset_test_c"]
        assert "_reset(" in ctx["reset_test_c"]
        assert "== 1.5" in ctx["reset_test_c"]

    def test_invalid_type_raises(self):
        import pytest

        with pytest.raises(ValueError, match="unsupported type"):
            make_state_ctx("comp", "Comp", [("x", "complex128", "0")])


class TestParseArrayType:
    def test_float_array(self):
        from just_makeit._types import parse_array_type

        assert parse_array_type("float[64]") == ("float", 64)

    def test_double_array(self):
        from just_makeit._types import parse_array_type

        assert parse_array_type("double[128]") == ("double", 128)

    def test_int32_array(self):
        from just_makeit._types import parse_array_type

        assert parse_array_type("int32_t[32]") == ("int32_t", 32)

    def test_complex_array(self):
        from just_makeit._types import parse_array_type

        assert parse_array_type("float _Complex[16]") == ("float _Complex", 16)

    def test_scalar_returns_none(self):
        from just_makeit._types import parse_array_type

        assert parse_array_type("double") is None

    def test_unknown_elem_returns_none(self):
        from just_makeit._types import parse_array_type

        assert parse_array_type("bad_type[8]") is None


class TestIsValidType:
    def test_scalar_valid(self):
        from just_makeit._types import is_valid_type

        assert is_valid_type("double")

    def test_array_valid(self):
        from just_makeit._types import is_valid_type

        assert is_valid_type("float[64]")

    def test_unknown_invalid(self):
        from just_makeit._types import is_valid_type

        assert not is_valid_type("complex128")

    def test_array_unknown_elem_invalid(self):
        from just_makeit._types import is_valid_type

        assert not is_valid_type("bad_type[8]")


class TestMakeStateCtxArrays:
    def _ctx(self, state_vars):
        return make_state_ctx("fir", "Fir", state_vars)

    def test_array_struct_field(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "float coeffs[16];" in ctx["state_struct_fields"]

    def test_mixed_struct_preserves_order(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("coeffs", "float[16]", None)])
        fields = ctx["state_struct_fields"]
        assert "double gain;" in fields
        assert "float coeffs[16];" in fields
        assert fields.index("double gain;") < fields.index("float coeffs[16];")

    def test_array_excluded_from_create_params(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("coeffs", "float[16]", None)])
        assert "coeffs" not in ctx["create_params"]
        assert "double gain" in ctx["create_params"]

    def test_array_only_create_params_void(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert ctx["create_params"] == "void"

    def test_array_create_assignment_uses_memset(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "memset(obj->coeffs, 0, sizeof(obj->coeffs))" in ctx["create_assignments"]
        )

    def test_array_reset_uses_memset(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "memset(state->coeffs, 0, sizeof(state->coeffs))"
            in ctx["reset_assignments"]
        )

    def test_array_getter_decl(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "fir_get_coeffs(const fir_state_t *state, float *dest);"
            in ctx["getter_setter_decls"]
        )

    def test_array_view_decl(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "const float *fir_get_coeffs_view(const fir_state_t *state);"
            in ctx["getter_setter_decls"]
        )

    def test_array_setter_decl(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "fir_set_coeffs(fir_state_t *state, const float *src);"
            in ctx["getter_setter_decls"]
        )

    def test_array_getter_impl_uses_memcpy(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "memcpy(dest, state->coeffs, 16 * sizeof(float))"
            in ctx["getter_setter_impls"]
        )

    def test_array_view_impl_returns_pointer(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "return state->coeffs;" in ctx["getter_setter_impls"]

    def test_array_setter_impl_uses_memcpy(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert (
            "memcpy(state->coeffs, src, 16 * sizeof(float))"
            in ctx["getter_setter_impls"]
        )

    def test_array_copy_getter_method_c(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "PyArray_SimpleNew(1, dims, NPY_FLOAT)" in ctx["getter_setter_methods_c"]
        assert "fir_get_coeffs(self->handle," in ctx["getter_setter_methods_c"]

    def test_array_view_getter_method_c(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "PyArray_SimpleNewFromData" in ctx["getter_setter_methods_c"]
        assert "NPY_ARRAY_WRITEABLE" in ctx["getter_setter_methods_c"]

    def test_array_setter_method_c_size_check(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "PyArray_SIZE(arr) != 16" in ctx["getter_setter_methods_c"]

    def test_array_pymethoddef_three_entries(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        pmd = ctx["getter_setter_pymethoddef"]
        assert '"get_coeffs"' in pmd
        assert '"get_coeffs_view"' in pmd
        assert '"set_coeffs"' in pmd

    def test_array_excluded_from_init_params_pyi(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("coeffs", "float[16]", None)])
        assert "coeffs" not in ctx["init_params_pyi"]
        assert "gain" in ctx["init_params_pyi"]

    def test_array_pyi_stubs_present(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        stubs = ctx["getter_setter_stubs_pyi"]
        assert "def get_coeffs(self) -> NDArray[np.float32]:" in stubs
        assert "def get_coeffs_view(self) -> NDArray[np.float32]:" in stubs
        assert "def set_coeffs(self, value: NDArray[np.float32]) -> None:" in stubs

    def test_array_pyi_view_docstring_warns(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "Do not use after destroy()" in ctx["getter_setter_stubs_pyi"]

    def test_array_excluded_from_create_args(self):
        ctx = self._ctx([("gain", "double", "1.0"), ("coeffs", "float[16]", None)])
        assert ctx["py_create_args"] == "1.0"
        assert ctx["c_create_args"] == "1.0"

    def test_array_getter_setter_test_py(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        test = ctx["getter_setter_test_py"]
        assert "set_coeffs" in test
        assert "get_coeffs" in test
        assert "get_coeffs_view" in test
        assert "WRITEABLE" in test

    def test_array_reset_test_py_zeros_after_reset(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "get_coeffs()[0] == _approx(0)" in ctx["reset_test_py"]

    def test_array_getter_setter_test_c(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        c = ctx["getter_setter_test_c"]
        assert "fir_set_coeffs(obj, src)" in c
        assert "fir_get_coeffs(obj, dst)" in c

    def test_array_reset_test_c_zeros_after_reset(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        c = ctx["reset_test_c"]
        assert "fir_set_coeffs(obj, ones)" in c
        assert "fir_reset(obj)" in c
        assert "buf[0] == 0.0f" in c

    def test_array_only_init_parse_block_suppresses_parse(self):
        ctx = self._ctx([("coeffs", "float[16]", None)])
        assert "PyArg_ParseTupleAndKeywords" not in ctx["init_parse_block"]
        assert "(void)args" in ctx["init_parse_block"]

    def test_complex_array_npy_enum(self):
        ctx = self._ctx([("buf", "double _Complex[8]", None)])
        assert "NPY_COMPLEX128" in ctx["getter_setter_methods_c"]


class TestNoStateWrapperNames:
    """Regression tests for gh#9 and gh#10."""

    def test_no_state_reset_uses_obj_prefix(self):
        # gh#9: the Python wrapper function declaration must use the Obj prefix
        # so it cannot match the C API name (e.g. Resampler_reset in core.h).
        ctx = make_state_ctx("Resampler", "Resampler", [], no_state=True)
        c = ctx["builtin_reset_c"]
        # The wrapper function name appears in the definition line.
        assert "ResamplerObj_reset(ResamplerObject *self" in c
        assert "ResamplerObj_reset" in ctx["builtin_reset_pmd"]
        # The body still calls the C API (lowercase or same-case) — that's fine.
        # We only care the *wrapper* name has the Obj suffix.
        assert "ResamplerObj_reset(ResamplerObject" in c

    def test_normal_state_reset_uses_component_prefix(self):
        ctx = make_state_ctx("fir", "Fir", [("gain", "double", "1.0")])
        assert "Fir_reset" in ctx["builtin_reset_c"]
        assert "Fir_reset" in ctx["builtin_reset_pmd"]
        assert "FirObj_reset" not in ctx["builtin_reset_c"]

    def test_no_state_extra_methods_use_obj_prefix(self):
        # gh#9: extra method wrappers must also use the Obj prefix.
        methods = [{"name": "process", "arg_type": "float", "return_type": "float"}]
        ctx = make_methods_ctx("Resampler", "Resampler", methods, no_state=True)
        assert "ResamplerObj_process" in ctx["extra_methods_c"]
        assert "ResamplerObj_process" in ctx["extra_methods_pymethoddef"]

    def test_normal_extra_methods_use_component_prefix(self):
        methods = [{"name": "process", "arg_type": "float", "return_type": "float"}]
        ctx = make_methods_ctx("fir", "Fir", methods, no_state=False)
        assert "Fir_process" in ctx["extra_methods_c"]

    def test_user_reset_suppresses_builtin_reset(self):
        # gh#10: if user defines reset in methods, the builtin must be cleared.
        methods = [{"name": "reset", "arg_type": "void", "return_type": "void"}]
        ctx = make_methods_ctx("Resampler", "Resampler", methods, no_state=True)
        assert ctx["builtin_reset_c"] == ""
        assert ctx["builtin_reset_pmd"] == ""

    def test_user_reset_suppresses_builtin_normal_object(self):
        # Suppression also applies to stateful objects.
        methods = [{"name": "reset", "arg_type": "void", "return_type": "void"}]
        ctx = make_methods_ctx("fir", "Fir", methods, no_state=False)
        assert ctx["builtin_reset_c"] == ""
        assert ctx["builtin_reset_pmd"] == ""

    def test_no_user_reset_does_not_suppress_builtin(self):
        methods = [{"name": "process", "arg_type": "float", "return_type": "float"}]
        ctx = make_methods_ctx("fir", "Fir", methods, no_state=False)
        assert "builtin_reset_c" not in ctx or ctx.get("builtin_reset_c") != ""


class TestVariableOutputComplexParam:
    """Regression test for gh#11."""

    def test_complex_param_uses_raw_and_to_c(self):
        # A variable_output method with a double _Complex param must declare
        # Py_complex x_raw, parse into it, then convert to double complex x.
        methods = [
            {
                "name": "push_ptr",
                "arg_type": "void",
                "return_type": "double _Complex",
                "variable_output": True,
                "params": [{"name": "x", "type": "double _Complex"}],
            }
        ]
        ctx = make_methods_ctx("delay", "Delay", methods)
        c = ctx["extra_methods_c"]
        assert "Py_complex x_raw = {0.0, 0.0}" in c
        assert "double complex x = x_raw.real + x_raw.imag * I" in c
        assert "= 0;" not in c or "x_raw = {0.0, 0.0}" in c

    def test_float_complex_param_uses_raw_and_to_c(self):
        methods = [
            {
                "name": "push",
                "arg_type": "void",
                "return_type": "float _Complex",
                "variable_output": True,
                "params": [{"name": "x", "type": "float _Complex"}],
            }
        ]
        ctx = make_methods_ctx("filt", "Filt", methods)
        c = ctx["extra_methods_c"]
        assert "Py_complex x_raw = {0.0, 0.0}" in c
        assert "float complex x = (float)x_raw.real + (float)x_raw.imag * I" in c


class TestInitParamsWithState:
    """gh-69: when ``[[obj.init_params]]`` is non-empty alongside ``state``,
    the ctor signature must come from init_params; state fields remain
    internal (struct + getters/setters) but are NOT exposed in __init__."""

    def _ctx(self, state_vars, init_params):
        return make_state_ctx(
            "reader",
            "Reader",
            state_vars,
            init_params=init_params,
        )

    def test_ctor_uses_init_params_not_state(self):
        ctx = self._ctx(
            [("fd", "int", "-1"), ("file_size", "size_t", "0")],
            [("filepath", "const char *", ""), ("hdr", "size_t", "0")],
        )
        # C signature reflects init_params, not state fields.
        assert "filepath" in ctx["create_params"]
        assert "hdr" in ctx["create_params"]
        assert "int fd" not in ctx["create_params"]
        assert "size_t file_size" not in ctx["create_params"]

    def test_state_fields_still_in_struct(self):
        ctx = self._ctx(
            [("fd", "int", "-1"), ("file_size", "size_t", "0")],
            [("filepath", "const char *", "")],
        )
        assert "int fd;" in ctx["state_struct_fields"]
        assert "size_t file_size;" in ctx["state_struct_fields"]

    def test_state_getters_setters_still_generated(self):
        ctx = self._ctx(
            [("fd", "int", "-1")],
            [("filepath", "const char *", "")],
        )
        assert "reader_get_fd" in ctx["getter_setter_decls"]
        assert "reader_set_fd" in ctx["getter_setter_decls"]

    def test_pyi_init_uses_init_params(self):
        ctx = self._ctx(
            [("fd", "int", "-1")],
            [("hdr", "size_t", "0")],
        )
        assert "hdr" in ctx["init_params_pyi"]
        assert "fd" not in ctx["init_params_pyi"]
