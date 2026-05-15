"""
test_object_helpers.py — unit tests for the three helper functions added in
v0.10.3 to fix module-scaffold gaps:

  _merge_module_init      (Gap #1)
  _extract_c_function_bodies  (Gap #3)
  _restore_c_function_bodies  (Gap #3)
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._object import (
    _extract_c_function_bodies,
    _merge_module_init,
    _restore_c_function_bodies,
)


# ---------------------------------------------------------------------------
# _merge_module_init
# ---------------------------------------------------------------------------


class TestMergeModuleInit:
    def test_adds_new_export(self):
        src = 'from .dsp import Nco\n__all__ = ["Nco"]\n'
        out = _merge_module_init(src, "dsp", ["Nco", "Mixer"])
        assert "from .dsp import Nco, Mixer" in out
        assert '"Nco"' in out and '"Mixer"' in out

    def test_idempotent_no_duplicates(self):
        src = 'from .dsp import Nco\n__all__ = ["Nco"]\n'
        out = _merge_module_init(src, "dsp", ["Nco"])
        assert out.count("Nco") == 2  # once in import, once in __all__

    def test_preserves_user_content_below_exports(self):
        src = (
            "from .dsp import Nco\n"
            '__all__ = ["Nco"]\n'
            "\n"
            "class NcoHelper:\n"
            "    '''User wrapper.'''\n"
        )
        out = _merge_module_init(src, "dsp", ["Nco", "Mixer"])
        assert "NcoHelper" in out
        assert "User wrapper" in out

    def test_empty_import_line_resolved(self):
        # Initial state after `just-makeit module foo` (no objects yet).
        src = "# foo/__init__.py\nfrom .foo import \n\n__all__ = []\n"
        out = _merge_module_init(src, "foo", ["Bar"])
        assert "from .foo import Bar" in out
        assert '"Bar"' in out
        # Must be valid Python.
        compile(out, "<string>", "exec")

    def test_no_import_line_appends_all(self):
        # File with no matching import line at all.
        src = "# hand-written\n\nclass Helper:\n    pass\n"
        out = _merge_module_init(src, "dsp", ["Nco"])
        assert '"Nco"' in out

    def test_all_missing_is_appended(self):
        src = "from .dsp import Nco\n"
        out = _merge_module_init(src, "dsp", ["Nco", "Mixer"])
        assert "__all__" in out

    def test_all_present_is_updated(self):
        src = 'from .dsp import Nco\n__all__ = ["Nco"]\n'
        out = _merge_module_init(src, "dsp", ["Nco", "Mixer"])
        assert '__all__ = ["Nco", "Mixer"]' in out

    def test_order_preserved_existing_first(self):
        src = 'from .dsp import Nco, Lo\n__all__ = ["Nco", "Lo"]\n'
        out = _merge_module_init(src, "dsp", ["Nco", "Lo", "Mixer"])
        import_line = [ln for ln in out.splitlines() if ln.startswith("from .dsp")][0]
        assert import_line == "from .dsp import Nco, Lo, Mixer"

    def test_empty_exports_list_returns_unchanged(self):
        src = 'from .dsp import Nco\n__all__ = ["Nco"]\n'
        out = _merge_module_init(src, "dsp", [])
        # No new names — nothing should change (or at minimum the import stays).
        assert "Nco" in out


# ---------------------------------------------------------------------------
# _extract_c_function_bodies
# ---------------------------------------------------------------------------

SIMPLE_BODY = """\
static PyObject *
Foo_bar(FooObject *self, PyObject *args)
{
    return PyLong_FromLong(42);
}
"""

UNUSED_PARAM_BODY = """\
static PyObject *
Foo_nop(FooObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_RETURN_NONE;
}
"""

NESTED_BRACE_BODY = """\
static PyObject *
Foo_branch(FooObject *self, PyObject *args)
{
    if (1) {
        return PyLong_FromLong(1);
    }
    return NULL;
}
"""

MULTI_BODY = SIMPLE_BODY + "\n" + UNUSED_PARAM_BODY


class TestExtractCFunctionBodies:
    def test_extracts_simple_function(self):
        result = _extract_c_function_bodies(SIMPLE_BODY)
        assert "Foo_bar" in result
        assert "PyLong_FromLong(42)" in result["Foo_bar"]

    def test_handles_py_unused_in_params(self):
        result = _extract_c_function_bodies(UNUSED_PARAM_BODY)
        assert "Foo_nop" in result
        assert "Py_RETURN_NONE" in result["Foo_nop"]

    def test_handles_nested_braces(self):
        result = _extract_c_function_bodies(NESTED_BRACE_BODY)
        assert "Foo_branch" in result
        body = result["Foo_branch"]
        assert body.count("{") == body.count("}")

    def test_extracts_multiple_functions(self):
        result = _extract_c_function_bodies(MULTI_BODY)
        assert set(result.keys()) == {"Foo_bar", "Foo_nop"}

    def test_empty_source_returns_empty(self):
        assert _extract_c_function_bodies("") == {}

    def test_no_static_pyobject_returns_empty(self):
        assert _extract_c_function_bodies("int main(void) { return 0; }") == {}

    def test_extracted_text_starts_with_signature(self):
        result = _extract_c_function_bodies(SIMPLE_BODY)
        assert result["Foo_bar"].startswith("static PyObject *\nFoo_bar(")

    def test_multiline_params(self):
        src = """\
static PyObject *
Foo_multi(
    FooObject *self,
    PyObject *args)
{
    Py_RETURN_NONE;
}
"""
        result = _extract_c_function_bodies(src)
        assert "Foo_multi" in result
        assert "Py_RETURN_NONE" in result["Foo_multi"]


# ---------------------------------------------------------------------------
# _restore_c_function_bodies
# ---------------------------------------------------------------------------

STUB = """\
static PyObject *
Foo_bar(FooObject *self, PyObject *args)
{
    PyErr_SetString(PyExc_NotImplementedError, "TODO");
    return NULL;
}
"""

IMPLEMENTED = """\
static PyObject *
Foo_bar(FooObject *self, PyObject *args)
{
    return PyLong_FromLong(99);
}
"""


class TestRestoreCFunctionBodies:
    def test_replaces_stub_with_preserved(self):
        out = _restore_c_function_bodies(STUB, {"Foo_bar": IMPLEMENTED})
        assert "PyLong_FromLong(99)" in out
        assert "NotImplementedError" not in out

    def test_unknown_name_not_replaced(self):
        out = _restore_c_function_bodies(STUB, {"Foo_other": IMPLEMENTED})
        assert "NotImplementedError" in out  # stub unchanged

    def test_empty_preserved_returns_source_unchanged(self):
        out = _restore_c_function_bodies(STUB, {})
        assert out == STUB

    def test_multiple_functions_restored_selectively(self):
        stub2 = """\
static PyObject *
Foo_nop(FooObject *self, PyObject *Py_UNUSED(ignored))
{
    PyErr_SetString(PyExc_NotImplementedError, "TODO");
    return NULL;
}
"""
        impl_nop = """\
static PyObject *
Foo_nop(FooObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_RETURN_NONE;
}
"""
        source = STUB + "\n" + stub2
        preserved = {"Foo_nop": impl_nop}  # only restore Foo_nop
        out = _restore_c_function_bodies(source, preserved)
        assert "Py_RETURN_NONE" in out  # Foo_nop restored
        assert "NotImplementedError" in out  # Foo_bar stub untouched

    def test_result_has_balanced_braces(self):
        out = _restore_c_function_bodies(STUB, {"Foo_bar": IMPLEMENTED})
        assert out.count("{") == out.count("}")

    def test_py_unused_in_new_source_still_matched(self):
        new_src = """\
static PyObject *
Foo_nop(FooObject *self, PyObject *Py_UNUSED(ignored))
{
    PyErr_SetString(PyExc_NotImplementedError, "TODO");
    return NULL;
}
"""
        impl = """\
static PyObject *
Foo_nop(FooObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_RETURN_NONE;
}
"""
        out = _restore_c_function_bodies(new_src, {"Foo_nop": impl})
        assert "Py_RETURN_NONE" in out
        assert "NotImplementedError" not in out
