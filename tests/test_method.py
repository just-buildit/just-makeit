"""Integration tests for `just-makeit method`."""

import re
import sys
from pathlib import Path

import pytest

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._object import run as object_run
from just_makeit._method import run as method_run
from just_makeit._apply import run as apply_run
from just_makeit._config import load, methods


class TestMethodPreservesInitParams:
    """gh-87: when an object is scaffolded with both `--state` and
    `--init-param` (e.g. a reader), the regenerated `_core.h` after a
    follow-up `jm method` must still use the init-param-driven ctor
    signature — not the state-driven one. Otherwise the header diverges
    from the existing `_core.c` (which still has the init-param ctor)
    and the build breaks."""

    def test_method_preserves_init_param_ctor(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(
            dest,
            "my_filter",
            module=None,
            state_vars=[
                ("gain", "float", "1.0f"),
                ("cutoff", "float", "0.5"),
            ],
            init_params=[("sample_rate", "float", "48000.0")],
            arg_type="float _Complex",
            return_type="float _Complex",
        )

        header = dest / "native" / "inc" / "my_filter" / "my_filter_core.h"
        before = header.read_text(encoding="utf-8")
        # Initial scaffold honours init_params (gh-69 from 0.13.22).
        assert "my_filter_create(float sample_rate)" in before
        assert "my_filter_create(float gain, float cutoff)" not in before

        # Add a method — _core.h gets regenerated; pre-gh-87 this drops
        # init_params and re-emits the state-driven signature.
        method_run(
            dest,
            "my_filter",
            "analyse",
            None,
            "void",
            "float",
            False,
            [],
        )

        after = header.read_text(encoding="utf-8")
        # The init-param ctor signature must survive the regeneration.
        assert "my_filter_create(float sample_rate)" in after, (
            "jm method dropped init_params during _core.h regeneration; "
            "header now diverges from _core.c. See gh-87."
        )
        assert "my_filter_create(float gain, float cutoff)" not in after


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
        cmake = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
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
        cmake = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
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
        cmake_after = (
            project / "native" / "src" / "nco" / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
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


class TestMethodOutKwarg:
    """gh-219: single-output variable_output methods gain an optional `out=`
    buffer (zero-alloc, caller-owned, safe to retain) and a <verb>_max_out()
    sibling, mirroring the blockwise steps(x, out=) path."""

    def _add(self, project, name="execute_cf32", arg="void", multi=None):
        method_run(
            project,
            "nco",
            name,
            None,
            arg,
            "float _Complex",
            True,
            multi or [],
        )
        return (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def test_wrapper_takes_keywords(self, project):
        ext = self._add(project, arg="float _Complex")
        assert "METH_VARARGS | METH_KEYWORDS" in ext
        assert "PyArg_ParseTupleAndKeywords" in ext
        assert '{"x", "out", NULL}' in ext

    def test_noarg_generator_takes_keywords(self, project):
        ext = self._add(project, arg="void")
        assert '{"count", "out", NULL}' in ext
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "|nO"' in ext

    def test_out_branch_validates_and_returns_prefix_view(self, project):
        ext = self._add(project, arg="float _Complex")
        # validation against max_out + writable contiguous buffer
        assert "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE" in ext
        assert "nco_execute_cf32_max_out(self->handle)" in ext
        assert "PyExc_ValueError" in ext
        # the returned view is pinned to the caller's array, not self
        assert "PyArray_SetBaseObject((PyArrayObject *)_oview," in ext
        assert "(PyObject *)out_arr)" in ext

    def test_out_validation_requires_max_of_max_out_and_call_size(self, project):
        """Follow-up to gh-219: `max_out()` is not always a true
        call-independent upper bound — a generator's `steps(count)` writes
        exactly `count` samples, which can exceed `max_out()`. Validating
        `out` against `max_out()` alone (no `max(max_out(), count)`) let an
        undersized `out=` buffer pass validation and then overflow in the
        kernel call. Both call shapes that reach the out= branch (an array
        arg, and a void-arg/no-params generator with an implicit `count`)
        must require capacity for whichever is larger."""
        ext = self._add(project, arg="float _Complex")
        assert "size_t _min_cap = _omax > (size_t)n ? _omax : ((size_t)n);" in ext
        assert "if (_cap < _min_cap) {" in ext
        assert "_cap, _min_cap);" in ext

        gen_ext = self._add(project, name="steps_ctrl", arg="void")
        assert (
            "size_t _min_cap = _omax > (size_t)n ? _omax : ((size_t)n);"
            in gen_ext
        )
        assert "if (_cap < _min_cap) {" in gen_ext

    def test_max_out_method_exposed(self, project):
        ext = self._add(project, arg="void")
        assert '{"execute_cf32_max_out"' in ext
        assert "PyLong_FromSize_t(" in ext
        assert "Nco_execute_cf32_max_out" in ext or (
            "nco_execute_cf32_max_out(self->handle)" in ext
        )

    def test_pyi_has_out_param_and_max_out(self, project):
        self._add(project, arg="float _Complex")
        pyi = (project / "src" / "dsp" / "nco.pyi").read_text(encoding="utf-8")
        assert "out:" in pyi and "| None = None" in pyi
        assert "def execute_cf32_max_out(self) -> int:" in pyi

    def test_multi_output_stays_positional(self, project):
        ext = self._add(project, arg="void", multi=["float _Complex"])
        # the method wrapper keeps the positional-only signature (no kwds);
        # `out_obj` elsewhere belongs to the object's built-in blockwise
        # steps(), which is the separate #197 precedent.
        assert (
            "Nco_execute_cf32(NcoObject *self,"
            " PyObject *args, PyObject *kwds)" not in ext
        )
        # no `out=`-sizing method is exposed (the C *_max_out() helper is still
        # called internally for the buffer alloc, hence the quoted-name check).
        assert '"execute_cf32_max_out"' not in ext


class TestVariableOutputParamsKeywords:
    """gh-412: a variable_output method with named params is positional-OR-
    keyword (kwlist built from the param names), matching its `.pyi`. It gets
    no `out=` buffer, but keyword parsing is independent of that feature —
    previously such a method fell through to a positional-only
    PyArg_ParseTuple, so `obj.delay(x, mu=0.3)` raised TypeError."""

    def _ext(self, project):
        # Farrow.delay-shaped: variable_output, an array input + a scalar.
        method_run(
            project,
            "nco",
            "delay",
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float _Complex[]"), ("mu", "double")],
        )
        return (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def test_binding_is_keyword_capable(self, project):
        ext = self._ext(project)
        assert "METH_VARARGS | METH_KEYWORDS" in ext
        assert "PyArg_ParseTupleAndKeywords(args, kwds," in ext
        assert '{"x", "mu", NULL}' in ext
        # positional-only parse must be gone for this method
        assert 'PyArg_ParseTuple(args, "Od"' not in ext

    def test_wrapper_signature_takes_kwds(self, project):
        ext = self._ext(project)
        assert (
            "Nco_delay(NcoObject *self, PyObject *args, PyObject *kwds)"
            in ext
        )

    def test_no_out_buffer_kwarg(self, project):
        # keyword-capable, but a params method still gets no `out=` feature:
        # no `<verb>_max_out()` sibling is exposed for delay (that marks the
        # gh-219 out= buffer path, which a params method does not take).
        ext = self._ext(project)
        assert '"delay_max_out"' not in ext


class TestVariableOutputSingleArrayParam:
    """gh-219 follow-up: a variable_output method whose primary array input
    is declared via `params=[{array}]` (arg_type="void", one array param,
    nothing else -- doppler's universal idiom for this shape, e.g.
    Despreader.steps(x)/BurstDemod.demod(x)/Specan.execute(x)) is otherwise
    identical to the bare-arg_type case for the purposes of `out=`. Before
    this fix, `has_params` blanket-excluded it, so `out=` never reached any
    real doppler object using this idiom."""

    def _ext(self, project, name="steps"):
        method_run(
            project,
            "nco",
            name,
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float _Complex[]")],
        )
        return (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def test_gets_out_kwarg(self, project):
        ext = self._ext(project)
        assert '"x", "out", NULL' in ext
        assert '"O|O"' in ext
        assert "PyObject *out_obj = NULL;" in ext

    def test_out_branch_present_with_max_of_max_out_and_call_size(
        self, project
    ):
        ext = self._ext(project)
        assert "if (out_obj && out_obj != Py_None) {" in ext
        assert "nco_steps_max_out(self->handle)" in ext
        assert "size_t _min_cap = _omax > (size_t)PyArray_SIZE(x_arr)" in ext
        assert "if (_cap < _min_cap) {" in ext

    def test_max_out_method_exposed(self, project):
        ext = self._ext(project)
        assert '"steps_max_out"' in ext
        assert "PyLong_FromSize_t(" in ext

    def test_pyi_has_out_param_and_max_out(self, project):
        self._ext(project)
        pyi = (project / "src" / "dsp" / "nco.pyi").read_text(
            encoding="utf-8"
        )
        assert "out:" in pyi and "| None = None" in pyi
        assert "def steps_max_out(self) -> int:" in pyi

    def test_returned_view_pinned_to_callers_array(self, project):
        ext = self._ext(project)
        assert "PyArray_SetBaseObject((PyArrayObject *)_oview," in ext
        assert "(PyObject *)out_arr)" in ext

    def test_genuine_multi_param_method_still_excluded(self, project):
        # Farrow.delay-shaped (x + mu): must NOT gain out= just because this
        # fix touched the has_params branch it also uses.
        method_run(
            project,
            "nco",
            "delay",
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float _Complex[]"), ("mu", "double")],
        )
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"delay_max_out"' not in ext
        # the delay-specific kwlist (2 params) stays as-is; the assertion
        # must be scoped to it -- nco's *default* scaffolded steps()/
        # execute() blockwise method separately has its own unrelated
        # {"x", "out", NULL} kwlist (gh-222's fixed 1:1 case) in this same
        # file, so a bare substring check would false-fail here.
        assert '_kwlist[] = {"x", "mu", NULL}' in ext
        assert '_kwlist[] = {"x", "mu", "out", NULL}' not in ext

    def test_no_placeholders(self, project):
        self._ext(project)
        _check_no_placeholders(project)


class TestMethodDeferredFree:
    """gh-219: the default zero-copy path must be use-after-free safe. On grow
    the old buffer is retired to a freelist (malloc-new, never
    realloc-in-place) and freed at dealloc, so any already-returned array
    aliasing it stays valid."""

    def _ext(self, project):
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
        return (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def test_retired_freelist_fields(self, project):
        ext = self._ext(project)
        assert "_execute_cf32_retired" in ext
        assert "_execute_cf32_retired_n" in ext
        assert "_execute_cf32_retired_cap" in ext

    def test_grow_uses_malloc_not_realloc_of_live_buffer(self, project):
        ext = self._ext(project)
        # the live output buffer is never realloc'd in place (that was the UAF)
        assert "realloc(self->_execute_cf32_buf" not in ext
        # a fresh buffer is malloc'd and the old one retired
        assert "malloc(_max * sizeof(" in ext
        assert (
            "self->_execute_cf32_retired[self->_execute_cf32_retired_n++]"
            " = self->_execute_cf32_buf" in ext
        )

    def test_dealloc_frees_retired_list(self, project):
        ext = self._ext(project)
        assert "free(self->_execute_cf32_retired[_i])" in ext
        assert "free(self->_execute_cf32_retired)" in ext


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
        if path.is_file() and path.suffix in (
            ".py",
            ".c",
            ".h",
            ".toml",
            ".txt",
        ):
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


# ---------------------------------------------------------------------------
# Regression: infrastructure functions (_dealloc, _init, ...) must never be
# body-preserved when regenerating a module fragment.  Before the fix,
# _restore_c_function_bodies would splice the old _dealloc / _init back in,
# silently dropping variable_output free() calls and multi_output secondary
# buffer allocs that the template had just generated correctly.
# ---------------------------------------------------------------------------


@pytest.fixture()
def module_project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    module_run(dest, "sig")
    object_run(dest, "nco", "sig", state_vars=[("freq", "double", "0.0")])
    return dest


def _nco_frag(module_project: Path) -> str:
    return (
        module_project / "native" / "src" / "sig" / "sig_ext_nco.c"
    ).read_text(encoding="utf-8")


class TestModuleInfraRegenOnMethod:
    """Adding a method to a module object must keep _dealloc / _init correct.

    The fragment for object 'nco' in module 'sig' is created (without any
    variable_output buffers) when the object is first registered.  When
    method_run is then called with variable_output / multi_output, the fresh
    template includes free() in _dealloc and malloc() in _init.  The bug was
    that _restore_c_function_bodies would overwrite those correct functions
    with the old (pre-method) bodies, silently breaking memory management.
    """

    def test_dealloc_has_free_after_variable_output_method(
        self, module_project
    ):
        method_run(
            module_project,
            "nco",
            "execute_cf32",
            "sig",
            "void",
            "float _Complex",
            True,
            [],
        )
        assert "free(self->_execute_cf32_buf)" in _nco_frag(module_project)

    def test_init_allocs_buf_after_variable_output_method(
        self, module_project
    ):
        method_run(
            module_project,
            "nco",
            "execute_cf32",
            "sig",
            "void",
            "float _Complex",
            True,
            [],
        )
        frag = _nco_frag(module_project)
        assert "_execute_cf32_buf" in frag
        assert "malloc(" in frag

    def test_dealloc_has_free_after_multi_output_method(self, module_project):
        method_run(
            module_project,
            "nco",
            "execute_iq",
            "sig",
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        frag = _nco_frag(module_project)
        assert "free(self->_execute_iq_buf)" in frag
        assert "free(self->_execute_iq_buf_1)" in frag

    def test_init_allocs_secondary_buf_after_multi_output_method(
        self, module_project
    ):
        method_run(
            module_project,
            "nco",
            "execute_iq",
            "sig",
            "void",
            "float _Complex",
            True,
            ["float _Complex"],
        )
        frag = _nco_frag(module_project)
        assert "_execute_iq_buf_1" in frag
        assert "malloc(_max * sizeof(float complex))" in frag


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
        text = (arr_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "const float complex *ctrl" in text

    def test_c_stub_has_len_param(self, arr_method):
        text = (arr_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "size_t ctrl_len" in text

    def test_c_stub_suppresses_ptr_and_len(self, arr_method):
        text = (arr_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "(void)ctrl;" in text
        assert "(void)ctrl_len;" in text

    def test_ext_c_has_pyarray_from_otf(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_FROM_OTF" in ext
        assert "NPY_COMPLEX64" in ext

    def test_ext_c_format_has_O(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"O"' in ext

    def test_ext_c_passes_ptr_and_len(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "ctrl_len" in ext

    def test_ext_c_has_decref(self, arr_method):
        ext = (arr_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Py_DECREF(ctrl_arr)" in ext

    def test_mixed_params_scalar_and_array(self, mixed_method):
        text = (mixed_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "float gain" in text
        assert "const float *buf" in text
        assert "size_t buf_len" in text

    def test_mixed_format_string(self, mixed_method):
        ext = (mixed_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"fO"' in ext

    def test_config_stores_array_type(self, arr_method):
        cfg = load(arr_method)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "process")
        assert m.get("params") == [
            {"name": "ctrl", "type": "float _Complex[]"}
        ]

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
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_FROM_OTF" in ext

    def test_ext_c_no_invalid_array_decl(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "float[] x" not in ext

    def test_ext_c_passes_ptr_and_len(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "x_len" in ext

    def test_ext_c_has_decref(self, add_method):
        ext = (add_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
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
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "x_arr" in ext
        assert "h_arr" in ext

    def test_ext_c_passes_x_to_c(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "x," in ext or "x_len" in ext

    def test_ext_c_passes_h_to_c(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "h_len" in ext

    def test_ext_c_format_has_two_O(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"OO"' in ext

    def test_ext_c_decrefs_both(self, madd_method):
        ext = (madd_method / "native/src/nco/nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Py_DECREF(x_arr)" in ext
        assert "Py_DECREF(h_arr)" in ext

    def test_no_placeholders(self, madd_method):
        _check_no_placeholders(madd_method)

    def test_core_c_stub_has_x_ptr(self, madd_method):
        core = (madd_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "const float *x" in core

    def test_core_c_stub_has_x_len(self, madd_method):
        core = (madd_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "size_t x_len" in core

    def test_core_c_stub_has_h_ptr(self, madd_method):
        core = (madd_method / "native/src/nco/nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "const float *h" in core

    def test_core_h_prototype_has_x_and_h(self, madd_method):
        hdr = (madd_method / "native/inc/nco/nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "const float *x" in hdr
        assert "const float *h" in hdr


class TestOutTypeScalarLength:
    """gh-65: a fixed-output method declared with `out_type` and a scalar
    integer param (no array param) must size the returned ndarray from the
    scalar param, not from a hardcoded ``0`` that produces an empty array."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["nco"], [("freq", "double", "0.0")])
        method_run(
            dest,
            "nco",
            "gen_samples",
            None,
            arg_type="void",
            return_type="void",
            variable_output=False,
            multi_output=[],
            params=[("n", "uint32_t")],
            out_type="float",
        )
        return dest

    def test_dims_uses_scalar_param(self, project):
        """``npy_intp _dims[]`` must reference the scalar param, not ``0``."""
        ext = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        # The buggy output is `{(npy_intp)0}` — the fix sizes from `n`.
        assert "(npy_intp)n" in ext
        assert "(npy_intp)0" not in ext


class TestMaxOutFlag:
    """Phase 2 row 2: --max-out N makes the generated
    ``<comp>_<name>_max_out`` return the literal N, no IMPLEMENT
    placeholder.  Users still hand-write the actual processing
    function — only the upper-bound stub is filled in."""

    def test_max_out_literal_in_core_c(self, project):
        method_run(
            project,
            "nco",
            "detect",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
            max_out=1024,
        )
        core_c = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        # Isolate the _max_out function body so we only assert about it,
        # not the rest of the file (which carries the step/detect IMPLEMENT
        # placeholders the user fills in).
        start = core_c.index("nco_detect_max_out(")
        end = core_c.index("}", start)
        body = core_c[start:end]
        assert "return 1024;" in body
        assert "<<IMPLEMENT" not in body
        assert "placeholder" not in body

    def test_max_out_persisted_in_config(self, project):
        method_run(
            project,
            "nco",
            "detect",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
            max_out=512,
        )
        cfg = load(project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "detect")
        assert m["max_out"] == 512

    def test_default_max_out_keeps_placeholder(self, project):
        """Without --max-out, the IMPLEMENT placeholder + `return 0;`
        stub stay intact (existing behaviour)."""
        method_run(
            project,
            "nco",
            "detect",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        core_c = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "<<IMPLEMENT" in core_c
        assert "return 0; /* placeholder */" in core_c


class TestMethodBoolParam:
    """gh-123: bool scalar param produces correct C stub and Python binding."""

    def test_bool_param_c_signature(self, project):
        method_run(
            project,
            "nco",
            "step_controlled",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            params=[("dump_now", "bool")],
        )
        core_c = (project / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "bool dump_now" in core_c

    def test_bool_param_ext_c_parse_block(self, project):
        method_run(
            project,
            "nco",
            "step_controlled",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            params=[("dump_now", "bool")],
        )
        ext_c = (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        # bool uses an int intermediate (parse_type) before cast
        assert "dump_now_raw" in ext_c
        assert "bool dump_now" in ext_c

    def test_bool_param_pyi_type_hint(self, project):
        method_run(
            project,
            "nco",
            "step_controlled",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            params=[("dump_now", "bool")],
        )
        pyi = (project / "src" / "dsp" / "nco.pyi").read_text(encoding="utf-8")
        assert "dump_now: bool" in pyi


class TestMethodExtraArgsTomlKey:
    """gh-123: extra_args in TOML is an alias for params on methods."""

    def test_extra_args_toml_replays_via_apply(self, tmp_path):
        """A method entry with extra_args survives jm apply unchanged."""
        proj = tmp_path / "dsp"
        new_run("dsp", proj, ["nco"], [("freq", "double", "0.0")])
        method_run(
            proj,
            "nco",
            "step_controlled",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            params=[("dump_now", "bool")],
        )

        # Rewrite TOML: replace 'params' with 'extra_args' in the method entry.
        toml_path = proj / "just-makeit.toml"
        toml_text = toml_path.read_text(encoding="utf-8")
        toml_text = toml_text.replace(
            'params = [{name = "dump_now", type = "bool"}]',
            'extra_args = [{name = "dump_now", type = "bool"}]',
        )
        toml_path.write_text(toml_text, encoding="utf-8")

        # Remove generated sacred files so apply must recreate them.
        import shutil

        shutil.rmtree(proj / "native" / "inc" / "nco")
        shutil.rmtree(proj / "native" / "src" / "nco")
        apply_run(proj)

        core_c = (proj / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )
        assert "bool dump_now" in core_c


class TestMethodVarargs:
    """--varargs generates a sacred per-method binding file compiled into the
    Python DSO; no typed C prototype is added to _core.h, and no stub is
    appended to _core.c."""

    @pytest.fixture()
    def varargs_project(self, project):
        method_run(
            project,
            "nco",
            "configure",
            None,
            "float",
            "float",
            False,
            [],
            varargs=True,
        )
        return project

    def _binding(self, p):
        return (
            p / "native" / "src" / "nco" / "nco_configure_core.c"
        ).read_text(encoding="utf-8")

    def _ext(self, p):
        return (p / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def _cmake(self, p):
        return (p / "native" / "src" / "nco" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )

    def _core_h(self, p):
        return (p / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )

    def _core_c(self, p):
        return (p / "native" / "src" / "nco" / "nco_core.c").read_text(
            encoding="utf-8"
        )

    def test_binding_file_created(self, varargs_project):
        assert (
            varargs_project / "native" / "src" / "nco" / "nco_configure_core.c"
        ).exists()

    def test_binding_file_has_python_h(self, varargs_project):
        assert "#include <Python.h>" in self._binding(varargs_project)

    def test_binding_file_has_pyobject_return(self, varargs_project):
        assert "PyObject *" in self._binding(varargs_project)

    def test_binding_file_has_implement_comment(self, varargs_project):
        assert "IMPLEMENT" in self._binding(varargs_project)

    def test_binding_file_includes_core_h(self, varargs_project):
        assert "nco_core.h" in self._binding(varargs_project)

    def test_binding_file_signature_has_args_kwargs(self, varargs_project):
        bt = self._binding(varargs_project)
        assert "PyObject *args" in bt
        assert "PyObject *kwargs" in bt

    def test_core_c_not_modified(self, varargs_project):
        # No stub appended — varargs lives in its own file.
        text = self._core_c(varargs_project)
        assert "nco_configure(" not in text

    def test_core_h_has_no_varargs_prototype(self, varargs_project):
        # No typed C prototype injected (binding is Python-side only).
        h = self._core_h(varargs_project)
        assert "nco_configure(" not in h

    def test_ext_c_has_extern_decl(self, varargs_project):
        assert "extern PyObject *" in self._ext(varargs_project)
        assert "nco_configure" in self._ext(varargs_project)

    def test_ext_c_has_meth_varargs_keywords(self, varargs_project):
        assert "METH_VARARGS | METH_KEYWORDS" in self._ext(varargs_project)

    def test_cmake_spliced_with_binding_file(self, varargs_project):
        assert "nco_configure_core.c" in self._cmake(varargs_project)

    def test_config_records_varargs_true(self, varargs_project):
        cfg = load(varargs_project)
        m = next(m for m in methods(cfg, "nco") if m["name"] == "configure")
        assert m.get("varargs") is True

    def test_no_placeholders(self, varargs_project):
        _check_no_placeholders(varargs_project)

    def test_pyi_has_star_args_signature(self, varargs_project):
        pyi = (varargs_project / "src" / "dsp" / "nco.pyi").read_text(
            encoding="utf-8"
        )
        assert "*args" in pyi
        assert "**kwargs" in pyi

    def test_bench_skips_varargs_method(self, varargs_project):
        from just_makeit._context._methods import _bench_method_block

        m = {"name": "configure", "varargs": True}
        assert _bench_method_block("nco", m) == ""

    def test_cmake_idempotent_second_add(self, varargs_project):
        """Splicing again when the file is already in CMakeLists is a no-op."""
        cmake_before = self._cmake(varargs_project)
        from just_makeit._method import _splice_varargs_source

        cmake_path = (
            varargs_project / "native" / "src" / "nco" / "CMakeLists.txt"
        )
        _splice_varargs_source(cmake_path, "nco", "nco_configure_core.c")
        assert cmake_path.read_text(encoding="utf-8") == cmake_before

    def test_varargs_replays_via_apply(self, tmp_path):
        """apply recreates the binding file and wires ext.c from TOML."""
        import shutil

        proj = tmp_path / "dsp"
        new_run("dsp", proj, ["nco"], [("freq", "double", "0.0")])
        method_run(
            proj,
            "nco",
            "configure",
            None,
            "float",
            "float",
            False,
            [],
            varargs=True,
        )

        # Remove glue files so apply must recreate them from TOML.
        shutil.rmtree(proj / "native" / "src" / "nco")
        apply_run(proj)

        binding = proj / "native" / "src" / "nco" / "nco_configure_core.c"
        assert binding.exists()
        assert "#include <Python.h>" in binding.read_text(encoding="utf-8")
        ext = (proj / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        assert "extern PyObject *" in ext
        assert "METH_VARARGS | METH_KEYWORDS" in ext


class TestMethodDefaultParams:
    """gh-240 (Phase A.2): a named method with params is keyword-capable, and a
    scalar param with a `default` is optional — after the `|` in the binding's
    PyArg_ParseTupleAndKeywords format, its C local seeded to the default, and
    rendered as `name: type = default` in the `.pyi`."""

    def _scaffold(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(
            dest,
            "filt",
            module=None,
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        method_run(
            dest,
            "filt",
            "apply_gain",
            None,
            "void",
            "void",
            False,
            [],
            params=[
                ("x", "float _Complex[]"),
                ("gain", "double", "2.0"),
            ],
        )
        return dest

    def test_binding_is_kw_capable_with_default(self, tmp_path):
        dest = self._scaffold(tmp_path)
        ext = (dest / "native/src/filt/filt_ext.c").read_text("utf-8")
        assert "PyObject *kwds)" in ext
        assert 'static char *_kwlist[] = {"x", "gain", NULL};' in ext
        assert '"O|d"' in ext  # x required, gain optional
        assert "double gain = 2.0;" in ext  # C local seeded with the default
        assert (
            "(PyCFunction)(void *)Filt_apply_gain, METH_VARARGS | METH_KEYWORDS"
            in ext
        )

    def test_pyi_shows_default(self, tmp_path):
        dest = self._scaffold(tmp_path)
        pyi = (dest / "src/dsp/filt.pyi").read_text("utf-8")
        assert "gain: float = 2.0" in pyi


class TestMethodSingleRecord:
    """gh-244 part 1: --single on a result_fields method returns ONE named
    record (PyStructSequence) by value, not a list[tuple]. The struct return
    type is user-defined; the binding lazily creates + caches the structseq
    type and SET_ITEMs each field."""

    def _scaffold(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_metrics_t",
            False,
            [],
            result_fields=[
                {"name": "snr", "type": "float"},
                {"name": "thd", "type": "float"},
                {"name": "nbins", "type": "uint32_t"},
            ],
            single=True,
        )
        return dest

    def test_binding_is_structseq_not_list(self, tmp_path):
        ext = (self._scaffold(tmp_path) / "native/src/tm/tm_ext.c").read_text(
            "utf-8"
        )
        assert "PyStructSequence_Field Tm_analyze_fields[]" in ext
        assert "PyStructSequence_NewType(&Tm_analyze_desc)" in ext
        assert "PyObject *_o = PyStructSequence_New(Tm_analyze_type);" in ext
        assert "PyStructSequence_SET_ITEM(_o, 0, PyFloat_FromDouble" in ext
        assert "tone_metrics_t _r = tm_analyze(self->handle," in ext
        assert "PyList_New" not in ext  # not the list[tuple] path

    def test_c_stub_returns_struct_by_value(self, tmp_path):
        core = (
            self._scaffold(tmp_path) / "native/src/tm/tm_core.c"
        ).read_text("utf-8")
        assert "tone_metrics_t" in core
        assert "tm_analyze(tm_state_t *state, const float complex *in," in core
        assert "return _r;" in core

    def test_pyi_is_tuple_of_field_types(self, tmp_path):
        pyi = (self._scaffold(tmp_path) / "src/p/tm.pyi").read_text("utf-8")
        assert (
            "def analyze(self, x: NDArray[np.complex64])"
            " -> tuple[float, float, int]:" in pyi
        )

    # gh-257: a single-record method WITH scalar params, one defaulted — the
    # doppler NPRMeasure.analyze(x, lo, hi, ..., guard_hz=0.0) shape that hit
    # bug #3 (the single binding ignored method params -> too-few-arguments).
    def _scaffold_with_params(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_metrics_t",
            False,
            [],
            params=[
                ("lo", "double"),
                ("hi", "double"),
                ("guard_hz", "double", "0.0"),
            ],
            result_fields=[
                {"name": "snr", "type": "float"},
                {"name": "thd", "type": "float"},
            ],
            single=True,
        )
        return dest

    def test_single_with_params_keyword_binding(self, tmp_path):
        ext = (
            self._scaffold_with_params(tmp_path) / "native/src/tm/tm_ext.c"
        ).read_text("utf-8")
        # input array + scalar params -> keyword-capable parse, default
        # applied, params threaded into the by-value structseq kernel call.
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "Odd|d",' in ext
        assert '{"x", "lo", "hi", "guard_hz", NULL}' in ext
        assert "double guard_hz = 0.0;" in ext
        assert "n_in, lo, hi, guard_hz);" in ext
        assert (
            '{"analyze", (PyCFunction)(void *)Tm_analyze,'
            " METH_VARARGS | METH_KEYWORDS," in ext
        )
        assert "PyStructSequence_New(Tm_analyze_type)" in ext
        assert "PyList_New" not in ext

    def test_single_with_params_survives_apply(self, tmp_path):
        # The #257 round-trip: jm apply must preserve `single` + the param
        # default when regenerating the binding from the manifest.
        dest = self._scaffold_with_params(tmp_path)
        apply_run(dest)
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert "double guard_hz = 0.0;" in ext
        assert "n_in, lo, hi, guard_hz);" in ext
        assert "PyStructSequence_New(Tm_analyze_type)" in ext
        assert "PyList_New" not in ext

    def test_single_with_params_pyi(self, tmp_path):
        pyi = (
            self._scaffold_with_params(tmp_path) / "src/p/tm.pyi"
        ).read_text("utf-8")
        assert (
            "def analyze(self, x: NDArray[np.complex64], lo: float,"
            " hi: float, guard_hz: float = 0.0) -> tuple[float, float]:" in pyi
        )

    def test_record_name_overrides_derived_structseq_name(self, tmp_path):
        # gh-257: a chosen public record name (ToneMetrics), independent of the
        # C return type (tone_metrics_t would derive "ToneMetrics" anyway, so
        # use a distinct name to prove the override).
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_meas_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            record_name="ToneMetrics",
        )
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert '"tm.ToneMetrics"' in ext  # chosen name
        assert '"tm.ToneMeas"' not in ext  # not the derived name

    def test_record_name_round_trips_and_survives_apply(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_meas_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            record_name="ToneMetrics",
        )
        # Persisted in the manifest (the generic unknown-key passthrough).
        cfg = load(dest)
        assert cfg["tm"]["methods"][0]["record_name"] == "ToneMetrics"
        # Survives a from-manifest regenerate: delete the binding, re-apply.
        (dest / "native/src/tm/tm_ext.c").unlink()
        apply_run(dest)
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert '"tm.ToneMetrics"' in ext

    def test_record_module_qualifies_structseq_module(self, tmp_path):
        # gh-261 item 2: record_module sets the structseq __module__ to the
        # project's import path instead of the C component name.
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_meas_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            record_name="ToneMetrics",
            record_module="my_pkg.dsp",
        )
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        # __module__ is everything before the last dot of the desc name.
        assert '"my_pkg.dsp.ToneMetrics"' in ext
        assert '"tm.ToneMetrics"' not in ext  # not the component name

    def test_record_module_unset_keeps_component_name(self, tmp_path):
        # No record_module -> historic behaviour (component-qualified), so
        # existing projects are byte-identical.
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_meas_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            record_name="ToneMetrics",
        )
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert '"tm.ToneMetrics"' in ext

    def test_record_module_round_trips_and_survives_apply(self, tmp_path):
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_meas_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            record_name="ToneMetrics",
            record_module="my_pkg.dsp",
        )
        cfg = load(dest)
        assert cfg["tm"]["methods"][0]["record_module"] == "my_pkg.dsp"
        (dest / "native/src/tm/tm_ext.c").unlink()
        apply_run(dest)
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert '"my_pkg.dsp.ToneMetrics"' in ext

    def test_single_params_no_input_array(self, tmp_path):
        # A single-record method with scalar params but NO input array
        # (arg_type void) -> keyword-only parse, no array handling.
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "calib",
            None,
            "void",
            "tone_metrics_t",
            False,
            [],
            params=[("lo", "double"), ("gain", "double", "1.0")],
            result_fields=[
                {"name": "snr", "type": "float"},
                {"name": "thd", "type": "float"},
            ],
            single=True,
        )
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "d|d",' in ext
        assert '{"lo", "gain", NULL}' in ext
        assert "double gain = 1.0;" in ext
        assert "tm_calib(self->handle, lo, gain);" in ext
        assert "PyStructSequence_New(Tm_calib_type)" in ext

    def test_single_nogil_releases_gil(self, tmp_path):
        # gh-261: a single-record method with nogil wraps the by-value kernel
        # in Py_BEGIN/END_ALLOW_THREADS, hoisting the array fetch above it and
        # keeping the input's Py_DECREF under the GIL after.
        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "tm",
            module=None,
            arg_type="float _Complex",
            return_type="void",
        )
        method_run(
            dest,
            "tm",
            "analyze",
            None,
            "float _Complex[]",
            "tone_metrics_t",
            False,
            [],
            result_fields=[{"name": "snr", "type": "float"}],
            single=True,
            nogil=True,
        )
        ext = (dest / "native/src/tm/tm_ext.c").read_text("utf-8")
        assert (
            "(const float complex *)PyArray_DATA(in_arr);" in ext
        )  # hoisted above the block
        assert "tone_metrics_t _r;" in ext  # declared outside the block
        assert "Py_BEGIN_ALLOW_THREADS" in ext
        assert "_r = tm_analyze(self->handle," in ext
        assert "Py_END_ALLOW_THREADS" in ext
        # the input's final Py_DECREF stays under the GIL, after the released
        # block (rindex skips the earlier error-path DECREF).
        assert ext.index("Py_END_ALLOW_THREADS") < ext.rindex(
            "Py_DECREF(in_arr)"
        )

    def test_single_without_nogil_holds_gil(self, tmp_path):
        # default (no nogil) keeps the inline call, no ALLOW_THREADS — unchanged.
        ext = (self._scaffold(tmp_path) / "native/src/tm/tm_ext.c").read_text(
            "utf-8"
        )
        assert "ALLOW_THREADS" not in ext
        assert "tone_metrics_t _r = tm_analyze(self->handle," in ext
