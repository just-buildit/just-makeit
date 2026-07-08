"""Integration tests for `just-makeit function`."""

from __future__ import annotations

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


def _fn_c(root, module, fn):
    """Read the per-function C stub file `native/src/<module>/<fn>.c`."""
    return (root / "native" / "src" / module / f"{fn}.c").read_text(
        encoding="utf-8"
    )


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


class TestFunctionOutParamConst:
    """gh-197: a module-level function array param marked `out = true` must
    render a writable `T *` (not `const T *`) in the binding. The `out`/`mutable`
    flags reach the renderer only as full param dicts; an earlier projection to
    (name, type) tuples silently dropped them and forced `const`."""

    def _wrappers(self, *, out: bool):
        from just_makeit._render import make_functions_ctx

        param = {"name": "output", "type": "float[]"}
        if out:
            param |= {"out": True, "mutable": True}
        fns = [
            {
                "name": "envelope_power",
                "doc": "power",
                "return_type": "void",
                "params": [
                    {"name": "input", "type": "float _Complex[]"},
                    param,
                    {"name": "n", "type": "size_t"},
                ],
            }
        ]
        return make_functions_ctx("dsp", "Dsp", fns)["function_wrappers"]

    def test_out_true_emits_writable_pointer(self):
        w = self._wrappers(out=True)
        assert "float *output = (float *)PyArray_DATA(output_arr);" in w
        assert "const float *output" not in w
        assert "NPY_ARRAY_WRITEABLE" in w

    def test_default_param_stays_const(self):
        w = self._wrappers(out=False)
        assert "const float *output = (const float *)" in w
        assert "NPY_ARRAY_WRITEABLE" not in w


class TestFunctionKeywordCapable:
    """gh-238: a module function with params is positional-OR-keyword —
    METH_VARARGS | METH_KEYWORDS + PyArg_ParseTupleAndKeywords with a kwlist of
    the param names. A no-param function stays METH_NOARGS. Keyword capability
    is ~free when callers pass positionally; only actual keyword use pays the
    match cost (the per-sample hot path step()/steps() stays positional-only)."""

    def _ctx(self, fns):
        from just_makeit._render import make_functions_ctx

        return make_functions_ctx("dsp", "Dsp", fns)

    def test_param_function_is_kw_capable(self):
        ctx = self._ctx(
            [
                {
                    "name": "scale_add",
                    "return_type": "void",
                    "params": [
                        {"name": "x", "type": "float _Complex[]"},
                        {"name": "gain", "type": "double"},
                        {"name": "bias", "type": "double"},
                    ],
                }
            ]
        )
        w = ctx["function_wrappers"]
        # 3-arg binding signature + kwds parse + kwlist of the param names
        assert (
            "_bind_scale_add(PyObject *self, PyObject *args, "
            "PyObject *kwds)" in w
        )
        assert 'static char *_kwlist[] = {"x", "gain", "bias", NULL};' in w
        assert "PyArg_ParseTupleAndKeywords(args, kwds," in w
        assert (
            "PyArg_ParseTuple(args," not in w
        )  # not the positional-only form
        # PyMethodDef: kw flags + the (void *) cast for the 3-arg signature
        assert (
            '{"scale_add", (PyCFunction)(void *)_bind_scale_add, '
            "METH_VARARGS | METH_KEYWORDS," in ctx["module_methods_def"]
        )

    def test_no_param_function_stays_noargs(self):
        ctx = self._ctx([{"name": "reset", "return_type": "void"}])
        # no kwds, no ParseTupleAndKeywords, plain METH_NOARGS entry
        assert "PyObject *kwds" not in ctx["function_wrappers"]
        assert "ParseTupleAndKeywords" not in ctx["function_wrappers"]
        assert (
            '{"reset", _bind_reset, METH_NOARGS,' in ctx["module_methods_def"]
        )


class TestFunctionDefaultParams:
    """gh-240: a scalar param with a `default` is optional — it goes after the
    `|` in the parse format and its C local is initialised to the default, so an
    omitted arg yields it."""

    def _wrappers(self, params):
        from just_makeit._render import make_functions_ctx

        fns = [{"name": "scaled", "return_type": "void", "params": params}]
        return make_functions_ctx("dsp", "Dsp", fns)["function_wrappers"]

    def test_default_scalar_is_optional_in_format(self):
        w = self._wrappers(
            [
                {"name": "x", "type": "float _Complex[]"},
                {"name": "gain", "type": "double", "default": "2.0"},
            ]
        )
        # required array before the `|`, defaulted scalar after it
        assert '"O|d"' in w
        assert "double gain = 2.0;" in w  # C local seeded with the default

    def test_two_defaults_after_one_bar(self):
        w = self._wrappers(
            [
                {"name": "x", "type": "float _Complex[]"},
                {"name": "gain", "type": "double", "default": "1.0"},
                {"name": "floor", "type": "double", "default": "-80.0"},
            ]
        )
        assert '"O|dd"' in w  # a single `|`, both scalars optional
        assert "double gain = 1.0;" in w
        assert "double floor = -80.0;" in w

    def test_required_after_default_raises(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            self._wrappers(
                [
                    {"name": "gain", "type": "double", "default": "1.0"},
                    {"name": "n", "type": "size_t"},  # required after default
                ]
            )

    def test_pyi_shows_default(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"])
        function_run(
            root,
            "scaled",
            "dsp",
            params=[
                ("x", "float _Complex[]"),
                ("gain", "double", False, "2.0"),
            ],
            return_type="void",
        )
        pyi = (root / "src/dsp/dsp/dsp.pyi").read_text(encoding="utf-8")
        assert "gain: float = 2.0" in pyi


class TestFunctionOutParamRoundTrip:
    """gh-221: an `out` param supplied to `function_run` as a 3-tuple
    `(name, type, is_out)` (the shape the CLI's `--out-param` builds) must
    persist `out = true` to the manifest and render a writable pointer — the
    CLI/run seam, not just the renderer (gh-197) or the CLI parser."""

    def test_three_tuple_out_param_persists_and_renders(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["dsp"])
        function_run(
            root,
            "envelope_power",
            "dsp",
            params=[
                ("input", "float _Complex[]"),
                ("output", "float[]", True),
                ("n", "size_t"),
            ],
            return_type="void",
        )
        # Persisted to the manifest as out = true.
        cfg = load(root)
        fns = cfg_module_functions(cfg, "dsp")
        out_p = next(p for p in fns[0]["params"] if p["name"] == "output")
        assert out_p.get("out") is True
        # Rendered as a writable, non-const pointer; the read-only input
        # param keeps its const qualifier.
        c = _fn_c(root, "dsp", "envelope_power")
        assert "float *output" in c
        assert "const float *output" not in c
        assert "const float complex *input" in c


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
    """just-makeit function writes each function stub to its own .c and
    declares it in _core.h."""

    def test_core_c_has_stub(self, fft_module):
        text = _fn_c(fft_module, "fft", "fft_global_setup")
        assert "fft_global_setup(void)" in text

    def test_core_c_has_implement_marker(self, fft_module):
        text = _fn_c(fft_module, "fft", "fft_global_setup")
        assert "<<IMPLEMENT: fft_global_setup>>" in text

    def test_core_c_has_return_none_equivalent(self, fft_module):
        # void function: no return statement, just empty body
        text = _fn_c(fft_module, "fft", "fft_global_setup")
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
        assert "static PyMethodDef fft_module_methods[]" in ext

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
        assert ".m_methods = fft_module_methods," in ext

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
    def test_both_stubs_in_own_files(self, two_functions):
        setup = _fn_c(two_functions, "fft", "fft_global_setup")
        assert "fft_global_setup(void)" in setup
        execute = _fn_c(two_functions, "fft", "fft1d_execute")
        assert "fft1d_execute(void)" in execute

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
        assert "static PyMethodDef dsp_module_methods[]" in ext
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


class TestCollocatedModuleFunction:
    """gh: a module whose name equals one of its objects (the collocated
    case) plus a module-level function must not emit two PyMethodDef tables
    with the same name.  The aggregator <mod>_ext.c #includes the object
    fragment <mod>_ext_<obj>.c into one translation unit, so the module-level
    table (named ``<mod>_module_methods``) and the object's own
    ``<Component>_methods`` table would collide as a duplicate symbol if both
    were title-cased to ``<Module>_methods``."""

    @pytest.fixture()
    def collocated(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, modules=["fft"])
        object_run(
            root,
            "fft",
            "fft",
            state_vars=[("n", "int", "8")],
            arg_type="float",
            return_type="float",
        )
        function_run(
            root, "setup", "fft", params=[("k", "int")], return_type="void"
        )
        return root

    def test_method_tables_have_distinct_names(self, collocated):
        src = collocated / "native" / "src" / "fft"
        agg = (src / "fft_ext.c").read_text(encoding="utf-8")
        frag = (src / "fft_ext_fft.c").read_text(encoding="utf-8")
        # The module-level table is module-named; the object's is type-named.
        assert "static PyMethodDef fft_module_methods[]" in agg
        assert ".m_methods = fft_module_methods," in agg
        assert "static PyMethodDef Fft_methods[]" in frag

    def test_no_duplicate_pymethoddef_symbol(self, collocated):
        # The aggregator includes the fragment into one TU; collect every
        # PyMethodDef table name from both and ensure none repeats.
        src = collocated / "native" / "src" / "fft"
        names: list[str] = []
        for fname in ("fft_ext.c", "fft_ext_fft.c"):
            text = (src / fname).read_text(encoding="utf-8")
            names += re.findall(r"static PyMethodDef (\w+)\[\]", text)
        assert len(names) == len(set(names)), (
            f"duplicate PyMethodDef symbol across the module TU: {names}"
        )


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
    """--param name:type generates a typed C stub in its own <fn>.c and a
    wrapper in _ext.c."""

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
        text = _fn_c(typed_fn, "fft", "compute_window")
        assert "compute_window" in text

    def test_core_c_has_named_params(self, typed_fn):
        text = _fn_c(typed_fn, "fft", "compute_window")
        assert "size_t n" in text
        assert "float beta" in text

    def test_core_c_suppresses_params(self, typed_fn):
        text = _fn_c(typed_fn, "fft", "compute_window")
        assert "(void)n;" in text
        assert "(void)beta;" in text

    def test_core_c_has_placeholder_return(self, typed_fn):
        text = _fn_c(typed_fn, "fft", "compute_window")
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
        text = _fn_c(root, "fft", "reset_fft")
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
        text = _fn_c(fft_module, "fft", "fft_global_setup")
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
    """--param name:type[] generates numpy array parse in _ext.c, ptr/len in
    the function's own <fn>.c."""

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
        text = _fn_c(arr_fn, "fft", "apply_window")
        assert "const float complex *data" in text

    def test_core_c_has_len_param(self, arr_fn):
        text = _fn_c(arr_fn, "fft", "apply_window")
        assert "size_t data_len" in text

    def test_core_c_suppresses_ptr_and_len(self, arr_fn):
        text = _fn_c(arr_fn, "fft", "apply_window")
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
        text = _fn_c(mixed_fn, "fft", "scale_buffer")
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
        """A regular (non-inline) function goes in its own <fn>.c file."""
        root = tmp_path / "dsp"
        new_run("dsp", root, modules=["fft"])
        function_run(root, "fft_setup", "fft", return_type="void")
        c = _fn_c(root, "fft", "fft_setup")
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
        c = _fn_c(out_param_fn, "io", "convert")
        assert "float *output" in c
        assert "const float *output" not in c
        assert "const float *input" in c

    def test_toml_round_trips_out_flag(self, out_param_fn):
        cfg = (out_param_fn / "just-makeit.toml").read_text(encoding="utf-8")
        # The dumped manifest preserves `out = true` for the output param.
        assert "out = true" in cfg


class TestVariableOutputFunction:
    """gh-318: a module function may allocate its OWN self-sized 1-D output
    (variable_output=true) — distinct from sizing to an input array's length or
    a caller-supplied `out=true` buffer. The length is an `out_size` C expr over
    the args (incl. array `<name>_len`s); `out` is appended last to the call; a
    size_t return trims, a void return keeps the full allocation. Keeps a
    helper like rrc_taps(beta, sps, span) -> ndarray zero-Python."""

    def _w(self, fn):
        from just_makeit._render import make_functions_ctx

        return make_functions_ctx("wfm", "Wfm", [fn])["function_wrappers"]

    def test_scalar_args_self_sized_void(self):
        w = self._w(
            {
                "name": "rrc_taps",
                "return_type": "void",
                "out_type": "float",
                "variable_output": True,
                "out_size": "rrc_ntaps(sps, span)",
                "params": [
                    {"name": "beta", "type": "double"},
                    {"name": "sps", "type": "int"},
                    {"name": "span", "type": "int"},
                ],
            }
        )
        # length from the out_size expr; allocate; out appended LAST; full return.
        assert "npy_intp _dim = (npy_intp)(rrc_ntaps(sps, span));" in w
        assert "PyArray_EMPTY(1, &_dim, NPY_FLOAT, 0)" in w
        assert (
            "rrc_taps(beta, sps, span, "
            "(float *)PyArray_DATA((PyArrayObject *)_out));" in w
        )
        assert "return _out;" in w
        assert "PyArray_DIMS" not in w  # void → no trim

    def test_array_in_out_size_references_array_len(self):
        w = self._w(
            {
                "name": "upsample",
                "return_type": "void",
                "out_type": "float",
                "variable_output": True,
                "out_size": "x_len * factor",
                "params": [
                    {"name": "x", "type": "float[]"},
                    {"name": "factor", "type": "int"},
                ],
            }
        )
        # input array marshaled; out_size uses its `_len`; cleanup before return.
        assert "size_t x_len = (size_t)PyArray_SIZE(x_arr);" in w
        assert "npy_intp _dim = (npy_intp)(x_len * factor);" in w
        assert (
            "upsample(x, x_len, factor, "
            "(float *)PyArray_DATA((PyArrayObject *)_out));" in w
        )
        assert "Py_DECREF(x_arr);" in w

    def test_size_t_return_trims_to_count(self):
        w = self._w(
            {
                "name": "compact",
                "return_type": "size_t",
                "out_type": "float",
                "variable_output": True,
                "out_size": "n",
                "params": [{"name": "n", "type": "int"}],
            }
        )
        # a counting fn: allocate the cap, then trim the array to the return.
        assert "size_t _n = (size_t)compact(n, " in w
        assert "PyArray_DIMS((PyArrayObject *)_out)[0] = (npy_intp)_n;" in w

    def test_round_trips_through_toml(self, tmp_path):
        # out_size survives save()->load() (the generic gh-257 path).
        from just_makeit import _config as C

        cfg = {
            "project": {"name": "p", "version": "0.1.0"},
            "module": {
                "wfm": {
                    "functions": [
                        {
                            "name": "rrc_taps",
                            "return_type": "void",
                            "out_type": "float",
                            "variable_output": True,
                            "out_size": "rrc_ntaps(sps, span)",
                            "params": [{"name": "sps", "type": "int"}],
                        }
                    ]
                }
            },
        }
        C.save(tmp_path, cfg)
        fn = C.module_functions(C.load(tmp_path), "wfm")[0]
        assert fn.get("variable_output") in (True, "true")
        assert fn["out_size"] == "rrc_ntaps(sps, span)"


class TestModuleFunctionDocstring:
    """gh-384: a module free function whose sacred ``<module>_core.h`` carries
    hand-written Doxygen (``@brief``/``@param``/``@code``) gets a full
    synthesized ``.pyi`` docstring with a runnable ``Examples`` doctest — the
    same treatment object methods get. With no header block (a fresh scaffold
    injects a decl only) it keeps the historical one-line stub, so a
    manifest-only rebuild is unchanged (idempotence)."""

    def _scaffold(self, root):
        new_run("dsp", root, modules=["dsp"])
        function_run(
            root,
            "enbw",
            "dsp",
            params=[("a", "double")],
            return_type="double",
        )

    def test_scaffold_function_keeps_name_stub(self, tmp_path):
        from just_makeit._stubs import make_module_pyi

        root = tmp_path / "dsp"
        self._scaffold(root)
        pyi = make_module_pyi(load(root), "dsp", root)
        assert '"""Enbw."""' in pyi

    def test_header_code_becomes_examples_doctest(self, tmp_path):
        from just_makeit._stubs import make_module_pyi

        root = tmp_path / "dsp"
        self._scaffold(root)

        # Prepend a hand-written Doxygen block before the injected declaration.
        header = root / "native" / "inc" / "dsp" / "dsp_core.h"
        text = header.read_text(encoding="utf-8")
        doc_block = (
            "/**\n"
            " * @brief Equivalent noise bandwidth of a window.\n"
            " * @param a A scalar input.\n"
            " * @return ENBW in bins.\n"
            " * @code\n"
            " * >>> 1 + 1\n"
            " * 2\n"
            " *\n"
            " * @endcode\n"
            " */\n"
        )
        lines = text.splitlines(keepends=True)
        for i, ln in enumerate(lines):
            if "enbw(" in ln and ln.rstrip().endswith(";"):
                lines.insert(i, doc_block)
                break
        else:  # pragma: no cover - the decl must exist after scaffold
            raise AssertionError("could not find the enbw declaration")
        header.write_text("".join(lines), encoding="utf-8")

        pyi = make_module_pyi(load(root), "dsp", root)
        assert "Equivalent noise bandwidth of a window." in pyi
        assert "Examples" in pyi
        assert ">>> 1 + 1" in pyi
        # No root -> historical one-line stub (back-compat / idempotence).
        assert "Examples" not in make_module_pyi(load(root), "dsp")


class TestVariableOutputArraySignature:
    """gh-385: a variable_output object method declared with an *element*
    arg_type — the documented blockwise shape, ``--arg-type 'float _Complex'
    --variable-output`` — consumes a *block*. Its generated binding parses a
    numpy array (PyArray_FROM_OTF) and its output already renders as NDArray,
    so the .pyi input annotation must be NDArray[...], not the scalar element
    (which the binding does not accept)."""

    def test_pyi_input_is_ndarray(self, tmp_path):
        from just_makeit._method import run as method_run

        root = tmp_path / "blk"
        new_run("blk", root, modules=["dsp"])
        object_run(
            root,
            "proc",
            module="dsp",
            no_state=True,
            no_step=True,
            class_name="Proc",
        )
        method_run(
            root,
            "proc",
            "execute",
            "dsp",
            arg_type="float _Complex",
            return_type="float _Complex",
            variable_output=True,
            multi_output=[],
        )
        pyi = (root / "src/blk/dsp/dsp.pyi").read_text(encoding="utf-8")
        # gh-423: this shape (bare arg_type, variable_output, no params, no
        # multi_output) is also `out=`-eligible (gh-219), so the module
        # aggregator's stub carries the optional `out=` buffer param.
        assert (
            "def execute(self, x: NDArray[np.complex64],"
            " out: NDArray[np.complex64] | None = None)"
            " -> NDArray[np.complex64]:" in pyi
        )
        assert "x: complex" not in pyi
