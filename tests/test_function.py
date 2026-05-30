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


class TestModuleScaffold:
    """just-makeit module creates _core.h and _core.c."""

    def test_core_h_exists(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        assert (root / "native/inc/fft/fft_core.h").exists()

    def test_core_c_exists(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        assert (root / "native/src/fft/fft_core.c").exists()

    def test_core_h_has_guard(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        text = (root / "native/inc/fft/fft_core.h").read_text(encoding="utf-8")
        assert "#ifndef FFT_CORE_H" in text
        assert "#define FFT_CORE_H" in text
        assert "#endif /* FFT_CORE_H */" in text

    def test_core_c_includes_header(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        text = (root / "native/src/fft/fft_core.c").read_text(encoding="utf-8")
        assert '#include "fft/fft_core.h"' in text


class TestCoreUpdated:
    """just-makeit function appends stubs to _core.c and _core.h."""

    def test_core_c_has_stub(self, fft_module):
        text = (fft_module / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "fft_global_setup(void)" in text

    def test_core_c_has_implement_marker(self, fft_module):
        text = (fft_module / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "<<IMPLEMENT: fft_global_setup>>" in text

    def test_core_c_has_return_none_equivalent(self, fft_module):
        # void function: no return statement, just empty body
        text = (fft_module / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "fft_global_setup(void)" in text

    def test_core_h_has_declaration(self, fft_module):
        text = (fft_module / "native/inc/fft/fft_core.h").read_text(
            encoding="utf-8"
        )
        assert "void fft_global_setup(void);" in text

    def test_core_h_declaration_before_endif(self, fft_module):
        text = (fft_module / "native/inc/fft/fft_core.h").read_text(
            encoding="utf-8"
        )
        decl_pos = text.index("void fft_global_setup(void);")
        endif_pos = text.index("#endif /* FFT_CORE_H */")
        assert decl_pos < endif_pos


class TestExtCHeader:
    def test_core_h_included_in_ext_c(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '#include "fft/fft_core.h"' in ext

    def test_core_h_included_after_numpy(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        numpy_pos = ext.index("#include <numpy/arrayobject.h>")
        include_pos = ext.index('#include "fft/fft_core.h"')
        assert numpy_pos < include_pos

    def test_core_h_omitted_without_functions(self, tmp_path):
        # Gap #5: phantom include — module-level core.h must NOT appear when
        # there are no module-level functions (it is only needed by those fns).
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        ext = (root / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert '#include "fft/fft_core.h"' not in ext


class TestExtCFooter:
    def test_pymethoddef_array_present(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "static PyMethodDef Fft_methods[]" in ext

    def test_pymethoddef_has_bind_wrapper(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"fft_global_setup", _bind_fft_global_setup' in ext

    def test_pymethoddef_has_sentinel(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "{NULL, NULL, 0, NULL}" in ext

    def test_m_methods_not_null(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert ".m_methods = Fft_methods," in ext

    def test_m_methods_null_without_functions(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        ext = (root / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert ".m_methods = NULL," in ext

    def test_doc_string_in_methoddef(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"Initialize FFT."' in ext

    def test_bind_wrapper_present_in_ext_c(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_bind_fft_global_setup(PyObject *self" in ext

    def test_noarg_bind_does_not_reference_unused_args(self, fft_module):
        """A no-param function binding must not emit `(void)args;`.

        The parameter is `Py_UNUSED(args)`, so there is no `args`
        identifier — `(void)args;` would be an undeclared-identifier
        compile error.
        """
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        body = ext.split("_bind_fft_global_setup(PyObject *self", 1)[1]
        body = body.split("}", 1)[0]
        assert "Py_UNUSED(args)" in body
        assert "(void)args;" not in body


class TestTwoFunctions:
    def test_both_stubs_in_core_c(self, two_functions):
        text = (two_functions / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "fft_global_setup(void)" in text
        assert "fft1d_execute(void)" in text

    def test_both_declarations_in_core_h(self, two_functions):
        text = (two_functions / "native/inc/fft/fft_core.h").read_text(
            encoding="utf-8"
        )
        assert "void fft_global_setup(void);" in text
        assert "void fft1d_execute(void);" in text

    def test_both_bind_wrappers_in_ext_c(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"fft_global_setup", _bind_fft_global_setup' in ext
        assert '"fft1d_execute", _bind_fft1d_execute' in ext

    def test_first_entry_before_second(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert ext.index("fft_global_setup") < ext.index("fft1d_execute")

    def test_second_doc_in_methoddef(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"Execute 1-D FFT."' in ext

    def test_default_doc_when_no_doc(self, two_functions):
        ext = (two_functions / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
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
        assert [f["name"] for f in fns] == [
            "fft_global_setup",
            "fft1d_execute",
        ]

    def test_config_empty_when_no_functions(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        cfg = load(root)
        assert cfg_module_functions(cfg, "fft") == []

    def test_toml_serializes_functions_section(self, fft_module):
        toml_text = (fft_module / "just-makeit.toml").read_text(
            encoding="utf-8"
        )
        assert "[[module.fft.functions]]" in toml_text
        assert 'name = "fft_global_setup"' in toml_text
        assert 'doc = "Initialize FFT."' in toml_text


class TestCoexistenceWithObjects:
    def test_objects_and_functions_both_present(
        self, module_with_objects_and_functions
    ):
        root = module_with_objects_and_functions
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert "NcoType" in ext
        assert "static PyMethodDef Dsp_methods[]" in ext
        assert '"global_setup", _bind_global_setup' in ext

    def test_core_h_included_with_objects(
        self, module_with_objects_and_functions
    ):
        root = module_with_objects_and_functions
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert '#include "dsp/dsp_core.h"' in ext

    def test_adding_object_after_function_preserves_methods(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"])
        function_run(root, "global_setup", "dsp")
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert "_bind_global_setup" in ext
        assert "NcoType" in ext

    def test_adding_function_after_object_preserves_object(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        function_run(root, "global_setup", "dsp")
        ext = (root / "native/src/dsp/dsp_ext.c").read_text(encoding="utf-8")
        assert "_bind_global_setup" in ext
        assert "NcoType" in ext


class TestValidation:
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


class TestFunctionTyped:
    """--param name:type generates a typed C stub in _core.c and wrapper in _ext.c."""

    @pytest.fixture()
    def typed_fn(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "compute_window",
            "fft",
            params=[("n", "size_t"), ("beta", "float")],
            return_type="float",
        )
        return root

    def test_core_c_has_stub(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "compute_window" in text

    def test_core_c_has_named_params(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "size_t n" in text
        assert "float beta" in text

    def test_core_c_suppresses_params(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "(void)n;" in text
        assert "(void)beta;" in text

    def test_core_c_has_placeholder_return(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "return (float)" in text

    def test_core_h_has_declaration(self, typed_fn):
        text = (typed_fn / "native/inc/fft/fft_core.h").read_text(
            encoding="utf-8"
        )
        assert "float compute_window(size_t n, float beta);" in text

    def test_ext_c_wrapper_has_parse_tuple(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        # size_t -> "K", float -> "f"
        assert '"Kf"' in text

    def test_ext_c_wrapper_calls_fn(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "compute_window(n, beta)" in text

    def test_ext_c_wrapper_returns_float(self, typed_fn):
        text = (typed_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyFloat_FromDouble" in text

    def test_complex_param_uses_raw_var(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "mix",
            "fft",
            params=[("z", "float _Complex")],
            return_type="float _Complex",
        )
        ext = (root / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert "z_raw" in ext
        assert '"D"' in ext

    def test_void_return_no_return_stmt_in_core(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "reset_fft",
            "fft",
            params=[("n", "size_t")],
            return_type="void",
        )
        text = (root / "native/src/fft/fft_core.c").read_text(encoding="utf-8")
        assert "return (void)" not in text

    def test_void_return_py_return_none_in_ext(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "reset_fft",
            "fft",
            params=[("n", "size_t")],
            return_type="void",
        )
        ext = (root / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert "Py_RETURN_NONE" in ext

    def test_no_params_generates_void_stub(self, fft_module):
        text = (fft_module / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "fft_global_setup(void)" in text

    def test_no_params_wrapper_uses_noargs(self, fft_module):
        ext = (fft_module / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "METH_NOARGS" in ext

    def test_config_stores_params_and_return_type(self, typed_fn):
        cfg = load(typed_fn)
        fns = cfg_module_functions(cfg, "fft")
        fn = next(f for f in fns if f["name"] == "compute_window")
        assert fn["params"] == [
            {"name": "n", "type": "size_t"},
            {"name": "beta", "type": "float"},
        ]
        assert fn["return_type"] == "float"

    def test_config_no_return_type_for_void(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "reset_fft",
            "fft",
            params=[("n", "size_t")],
            return_type="void",
        )
        cfg = load(root)
        fns = cfg_module_functions(cfg, "fft")
        fn = next(f for f in fns if f["name"] == "reset_fft")
        assert "return_type" not in fn

    def test_no_stray_placeholders_typed(self, typed_fn):
        for path in typed_fn.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
            ):
                m = _STRAY_PLACEHOLDER.search(path.read_text(encoding="utf-8"))
                assert m is None, f"Stray placeholder in {path}"


class TestFunctionWithArrayParam:
    """--param name:type[] generates numpy array parse in _ext.c, ptr/len in _core.c."""

    @pytest.fixture()
    def arr_fn(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "apply_window",
            "fft",
            params=[("data", "float _Complex[]")],
            return_type="void",
        )
        return root

    @pytest.fixture()
    def mixed_fn(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(
            root,
            "scale_buffer",
            "fft",
            params=[("gain", "float"), ("buf", "float[]")],
            return_type="void",
        )
        return root

    def test_core_c_has_const_ptr_param(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "const float complex *data" in text

    def test_core_c_has_len_param(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "size_t data_len" in text

    def test_core_c_suppresses_ptr_and_len(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "(void)data;" in text
        assert "(void)data_len;" in text

    def test_core_h_declaration(self, arr_fn):
        text = (arr_fn / "native/inc/fft/fft_core.h").read_text(
            encoding="utf-8"
        )
        assert "apply_window" in text
        assert "const float complex *data" in text

    def test_ext_c_has_pyarray_from_otf(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_FROM_OTF" in text
        assert "NPY_COMPLEX64" in text

    def test_ext_c_format_has_O(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"O"' in text

    def test_ext_c_passes_ptr_and_len(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "data_len" in text

    def test_ext_c_has_decref(self, arr_fn):
        text = (arr_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Py_DECREF(data_arr)" in text

    def test_mixed_scalar_and_array_in_core(self, mixed_fn):
        text = (mixed_fn / "native/src/fft/fft_core.c").read_text(
            encoding="utf-8"
        )
        assert "float gain" in text
        assert "const float *buf" in text
        assert "size_t buf_len" in text

    def test_mixed_format_string_in_ext(self, mixed_fn):
        text = (mixed_fn / "native/src/fft/fft_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"fO"' in text

    def test_config_stores_array_type(self, arr_fn):
        cfg = load(arr_fn)
        fns = cfg_module_functions(cfg, "fft")
        fn = next(f for f in fns if f["name"] == "apply_window")
        assert fn.get("params") == [
            {"name": "data", "type": "float _Complex[]"}
        ]

    def test_no_stray_placeholders(self, arr_fn):
        for path in arr_fn.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
            ):
                m = _STRAY_PLACEHOLDER.search(path.read_text(encoding="utf-8"))
                assert m is None, f"Stray placeholder in {path}"


class TestNoStrayPlaceholders:
    def _check(self, root: Path) -> None:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
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


# ---------------------------------------------------------------------------
# inline = True  (issue #23)
# ---------------------------------------------------------------------------


class TestInlineFunction:
    """inline=True emits static inline body in _core.h; nothing in _core.c."""

    @pytest.fixture()
    def inline_fn(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["cvt"])
        function_run(
            root,
            "f32_to_i16",
            "cvt",
            params=[("x", "float"), ("scale", "float")],
            return_type="int16_t",
            inline=True,
        )
        return root

    def test_core_h_has_static_inline(self, inline_fn):
        h = (inline_fn / "native/inc/cvt/cvt_core.h").read_text(
            encoding="utf-8"
        )
        assert "static inline" in h
        assert "f32_to_i16" in h

    def test_core_h_has_implement_comment(self, inline_fn):
        h = (inline_fn / "native/inc/cvt/cvt_core.h").read_text(
            encoding="utf-8"
        )
        assert "<<IMPLEMENT: f32_to_i16>>" in h

    def test_core_h_has_placeholder_return(self, inline_fn):
        h = (inline_fn / "native/inc/cvt/cvt_core.h").read_text(
            encoding="utf-8"
        )
        assert "return" in h and "placeholder" in h

    def test_core_c_has_no_entry(self, inline_fn):
        c = (inline_fn / "native/src/cvt/cvt_core.c").read_text(
            encoding="utf-8"
        )
        assert "f32_to_i16" not in c

    def test_core_h_has_no_bare_declaration(self, inline_fn):
        h = (inline_fn / "native/inc/cvt/cvt_core.h").read_text(
            encoding="utf-8"
        )
        # A bare forward declaration would have a semicolon-terminated signature
        # with no body.  The inline stub must NOT produce such a line.
        assert "int16_t f32_to_i16(float x, float scale);" not in h

    def test_ext_c_wrapper_present(self, inline_fn):
        # The Python binding calls the inline function just like any other.
        agg = (inline_fn / "native/src/cvt/cvt_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_bind_f32_to_i16" in agg

    def test_config_stores_inline_true(self, inline_fn):
        cfg = load(inline_fn)
        fns = cfg_module_functions(cfg, "cvt")
        fn = next(f for f in fns if f["name"] == "f32_to_i16")
        assert fn.get("inline") is True

    def test_toml_has_inline_true(self, inline_fn):
        toml = (inline_fn / "just-makeit.toml").read_text(encoding="utf-8")
        assert "inline = true" in toml

    def test_non_inline_function_unaffected(self, tmp_path):
        """A regular (non-inline) function still goes in _core.c."""
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(root, "fft_setup", "fft", return_type="void")
        c = (root / "native/src/fft/fft_core.c").read_text(encoding="utf-8")
        assert "fft_setup" in c
        h = (root / "native/inc/fft/fft_core.h").read_text(encoding="utf-8")
        assert "static inline" not in h

    def test_no_stray_placeholders(self, inline_fn):
        for path in inline_fn.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml"):
                m = _STRAY_PLACEHOLDER.search(path.read_text(encoding="utf-8"))
                assert m is None, f"Stray placeholder in {path}"


# ---------------------------------------------------------------------------
# out_type = "dtype[param]" scalar-sized output (gh-29)
# ---------------------------------------------------------------------------


class TestOutTypeScalarParam:
    """out_type = "float64[M]" generates an output array sized by scalar M."""

    @pytest.fixture()
    def scalar_sized(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["resample"])
        function_run(
            root,
            "ciccompmf",
            "resample",
            params=[
                ("N", "uint32_t"),
                ("R", "uint32_t"),
                ("M", "uint32_t"),
            ],
            return_type="void",
            out_type="float64[M]",
        )
        return root

    def test_binding_uses_scalar_len(self, scalar_sized):
        """Generated _bind_ciccompmf uses M (not array_len) as the dim."""
        ext = (scalar_sized / "native/src/resample/resample_ext.c").read_text(
            encoding="utf-8"
        )
        assert "(npy_intp)M" in ext

    def test_binding_allocates_double_array(self, scalar_sized):
        ext = (scalar_sized / "native/src/resample/resample_ext.c").read_text(
            encoding="utf-8"
        )
        assert "NPY_DOUBLE" in ext

    def test_stub_returns_ndarray_float64(self, scalar_sized):
        pyi = (scalar_sized / "src/dsp/resample/resample.pyi").read_text(
            encoding="utf-8"
        )
        assert "NDArray[np.float64]" in pyi

    def test_no_stray_placeholders(self, scalar_sized):
        for path in scalar_sized.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml"):
                m = _STRAY_PLACEHOLDER.search(path.read_text(encoding="utf-8"))
                assert m is None, f"Stray placeholder in {path}"


class TestOutArrayParamNotConst:
    """gh-72: array params marked ``out = true`` must drop the ``const``
    qualifier in both the header declaration and the implementation stub.
    Without this the generated impl can't write into the buffer (compile
    error: ``assignment of read-only location``) or the decl/impl signatures
    diverge."""

    @pytest.fixture()
    def out_param_fn(self, tmp_path):
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["io"])
        function_run(
            root,
            "convert",
            "io",
            params=[
                ("input", "float[]", False),
                ("output", "float[]", True),
                ("n", "size_t", False),
            ],
            return_type="void",
        )
        return root

    def test_decl_output_not_const(self, out_param_fn):
        h = (out_param_fn / "native/inc/io/io_core.h").read_text(
            encoding="utf-8"
        )
        # Output must be `float *output`, not `const float *output`.
        assert "float *output" in h
        assert "const float *output" not in h
        # Input must remain const.
        assert "const float *input" in h

    def test_impl_output_not_const(self, out_param_fn):
        c = (out_param_fn / "native/src/io/io_core.c").read_text(
            encoding="utf-8"
        )
        assert "float *output" in c
        assert "const float *output" not in c
        assert "const float *input" in c

    def test_toml_round_trips_out_flag(self, out_param_fn):
        cfg = (out_param_fn / "just-makeit.toml").read_text(encoding="utf-8")
        # The dumped manifest preserves `out = true` for the output param.
        assert "out = true" in cfg
