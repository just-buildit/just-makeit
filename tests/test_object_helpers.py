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
        import_line = [
            ln for ln in out.splitlines() if ln.startswith("from .dsp")
        ][0]
        assert import_line == "from .dsp import Nco, Lo, Mixer  # noqa: E402"

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


# ---------------------------------------------------------------------------
# require_static=False (gh-267: sacred _core.c/_core.h, public — non-static
# — functions)
# ---------------------------------------------------------------------------

CORE_C_SIMPLE = """\
foo_state_t *
foo_create(double gain)
{
    foo_state_t *obj = calloc(1, sizeof(*obj));
    obj->gain = gain;
    return obj;
}
"""

CORE_H_WITH_PROTOTYPE_AND_INLINE = """\
foo_state_t *foo_create(double gain);

void foo_destroy(foo_state_t *state);

static inline float
foo_step(const foo_state_t *state, float x)
{
    (void)state;
    return (float)x;
}
"""


class TestExtractCFunctionBodiesNonStatic:
    def test_extracts_public_function(self):
        result = _extract_c_function_bodies(
            CORE_C_SIMPLE, require_static=False
        )
        assert "foo_create" in result
        assert "obj->gain = gain;" in result["foo_create"]

    def test_ignores_bare_prototypes(self):
        # foo_create/foo_destroy are declaration-only (";", no body) here —
        # must not be captured, and must not corrupt the scan for foo_step.
        result = _extract_c_function_bodies(
            CORE_H_WITH_PROTOTYPE_AND_INLINE, require_static=False
        )
        assert "foo_create" not in result
        assert "foo_destroy" not in result
        assert "foo_step" in result
        assert result["foo_step"].count("{") == result["foo_step"].count("}")

    def test_require_static_true_ignores_public_functions(self):
        # Default behavior (ext.c call sites) must be unaffected.
        assert (
            _extract_c_function_bodies(CORE_C_SIMPLE, require_static=True)
            == {}
        )


class TestRestoreCFunctionBodiesSignatureGuard:
    def test_matching_signature_restores(self):
        new_source = """\
foo_state_t *
foo_create(double gain)
{
    foo_state_t *obj = calloc(1, sizeof(*obj));
    return obj;
}
"""
        preserved = {
            "foo_create": (
                "foo_state_t *\nfoo_create(double gain)\n{\n"
                "    foo_state_t *obj = calloc(1, sizeof(*obj));\n"
                "    obj->gain = gain; /* HAND */\n    return obj;\n}\n"
            )
        }
        out = _restore_c_function_bodies(
            new_source, preserved, require_static=False
        )
        assert "HAND" in out

    def test_mismatched_signature_skipped(self):
        # gh-267: a structural change (e.g. `jm add` growing the param
        # list) must fall back to the fresh body rather than splice an
        # incompatible one in — a hard compile break otherwise.
        new_source = """\
foo_state_t *
foo_create(double gain, int order)
{
    foo_state_t *obj = calloc(1, sizeof(*obj));
    obj->order = order;
    return obj;
}
"""
        preserved = {
            "foo_create": (
                "foo_state_t *\nfoo_create(double gain)\n{\n"
                "    foo_state_t *obj = calloc(1, sizeof(*obj));\n"
                "    obj->gain = gain; /* HAND */\n    return obj;\n}\n"
            )
        }
        out = _restore_c_function_bodies(
            new_source, preserved, require_static=False
        )
        assert "HAND" not in out
        assert "obj->order = order;" in out

    def test_require_static_false_ignores_declaration_only(self):
        # A bare prototype in new_source has no body to splice into.
        new_source = "foo_state_t *foo_create(double gain);\n"
        preserved = {"foo_create": CORE_C_SIMPLE}
        out = _restore_c_function_bodies(
            new_source, preserved, require_static=False
        )
        assert out == new_source
