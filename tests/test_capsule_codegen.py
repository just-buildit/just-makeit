"""Codegen tests for ``kind = "capsule"`` modules (gh-286).

Render the ``<module>_ext.c`` for a capsule module and assert the generated C
has the right shape — capsule mechanics, the create/execute/reset/destroy/
get_/set_ free functions, the GIL release, and the method table — against the
``ddc_fn`` reference shape (without needing a C compiler)."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _capsule


def _cfg(header=None, nogil=True):
    mod = {
        "kind": "capsule",
        "backing": "ddcr",
        "capsule_name": "doppler.ddc.ddcr_state",
        "init_params": [
            {"name": "norm_freq", "type": "double"},
            {"name": "rate", "type": "double"},
        ],
        "methods": [
            {
                "name": "execute",
                "arg_type": "float[]",
                "return_type": "float _Complex[]",
                "caller_out": True,
                "nogil": nogil,
            },
            {"name": "reset"},
        ],
        "properties": [
            {"name": "norm_freq", "type": "double", "writable": True},
            {"name": "rate", "type": "double"},
        ],
    }
    if header:
        mod["header"] = header
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "module": {"ddc_fn": mod},
    }


def _src(**kw):
    return _capsule.render_ext(_cfg(**kw), "ddc_fn")


class TestCapsuleMechanics:
    def test_capsule_payload_and_guards(self):
        s = _src()
        assert 'static const char _CAPS[] = "doppler.ddc.ddcr_state";' in s
        assert "ddcr_state_t *state;" in s
        assert "int                destroyed;" in s or "int destroyed;" in s
        assert "_wrap_destructor" in s and "_get_wrap" in s
        # use-after-destroy guard
        assert "has already been destroyed" in s

    def test_includes(self):
        s = _src()
        assert "#include <Python.h>" in s
        assert "#include <numpy/arrayobject.h>" in s
        assert '#include "ddcr/ddcr_core.h"' in s

    def test_header_override(self):
        s = _src(header="ddc/ddc_core.h")
        assert '#include "ddc/ddc_core.h"' in s
        assert "ddcr/ddcr_core.h" not in s


class TestFunctions:
    def test_create(self):
        s = _src()
        assert "_fn_ddcr_create(PyObject *mod, PyObject *args)" in s
        assert 'PyArg_ParseTuple(args, "dd", &norm_freq, &rate)' in s
        assert "w->state = ddcr_create(norm_freq, rate);" in s
        assert "PyCapsule_New(w, _CAPS, _wrap_destructor)" in s

    def test_execute_marshals_and_returns_view(self):
        s = _src()
        assert "_fn_ddcr_execute(PyObject *mod, PyObject *args)" in s
        assert "jm_array_arg(x_obj, NPY_FLOAT," in s
        assert "!= NPY_COMPLEX64" in s  # exact output dtype, no silent cast
        assert (
            "n_out = ddcr_execute(w->state, in_data, n_in, out_data, max_out);"
            in s
        )
        assert (
            "PySlice_New(NULL, stop, NULL)" in s
        )  # zero-copy view out[:n_out]

    def test_execute_releases_gil_when_nogil(self):
        assert "Py_BEGIN_ALLOW_THREADS" in _src(nogil=True)
        assert "Py_BEGIN_ALLOW_THREADS" not in _src(nogil=False)

    def test_reset_and_destroy(self):
        s = _src()
        assert "ddcr_reset(w->state);" in s
        assert "ddcr_destroy(w->state);" in s
        assert "w->destroyed = 1;" in s

    def test_property_accessors(self):
        s = _src()
        assert "PyFloat_FromDouble(ddcr_get_norm_freq(w->state))" in s  # read
        assert "ddcr_set_norm_freq(_w->state, norm_freq);" in s  # write
        assert "PyFloat_FromDouble(ddcr_get_rate(w->state))" in s
        # rate is read-only: no setter.
        assert "_fn_ddcr_set_rate" not in s


class TestModule:
    def test_method_table_lists_all_free_functions(self):
        s = _src()
        for fn in [
            "ddcr_create",
            "ddcr_execute",
            "ddcr_reset",
            "ddcr_destroy",
            "ddcr_get_norm_freq",
            "ddcr_set_norm_freq",
            "ddcr_get_rate",
        ]:
            assert f'{{"{fn}", _fn_{fn}, METH_VARARGS,' in s
        assert "{NULL, NULL, 0, NULL}" in s

    def test_module_init(self):
        s = _src()
        assert 'PyModuleDef_HEAD_INIT, "ddc_fn"' in s
        assert "PyMODINIT_FUNC\nPyInit_ddc_fn(void)" in s
        assert "import_array();" in s


_DECL = re.compile(
    r"^\s*(?:PyObject|_wrap_t|double|float|int|long|unsigned|u?int\d+_t"
    r"|size_t|Py_ssize_t|Py_complex|bool)\b[^(;]*(?:\([^;]*)?;",
    re.M,
)


def _declared(fn_text):
    """Every identifier a function body declares as a local."""
    names = []
    for stmt in _DECL.findall(fn_text):
        # drop initialisers, then read each declarator's trailing name
        stmt = re.sub(r"=[^,;]*", "", stmt).rstrip(";")
        names += [part.split()[-1].lstrip("*") for part in stmt.split(",")]
    return names


class TestASetterNamesNoPropertyLocal:
    """gh-2144: the setter converts into a local named for the property, so
    its own locals (the capsule, the value, the wrapper) must not be spelled
    like one a property can be called. ``double value`` beside the source
    object ``value`` is a redeclaration, and the module would not compile."""

    @pytest.mark.parametrize("name", ["value", "cap", "w"])
    @pytest.mark.parametrize("ptype", ["double", "int8_t", "float _Complex"])
    def test_no_local_is_declared_twice(self, name, ptype):
        src = _capsule._emit_getset(
            "b", "sym", {"name": name, "type": ptype, "writable": True}, "m"
        )
        setter = src[src.index(f"_fn_b_set_{name}") :]
        names = _declared(setter)
        assert name in names, setter
        dup = sorted({n for n in names if names.count(n) > 1})
        assert not dup, (dup, setter)
