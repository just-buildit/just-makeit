"""gh-627: a module object's FIRST property never reached its binding.

`jm apply` replays a manifest-declared property into a temp scaffold, then
`_docsync.transplant_missing_bindings` splices members the real fragment is
missing. That splice was additive-into-an-existing-array: it adds a row before
the array's `{NULL}` sentinel. An object with no properties has no
`PyGetSetDef` array at all, so there was nothing to splice against and the
whole declaration was dropped.

The damage was the silence. The `.pyi` still gained the property, so the stub
advertised a member the extension did not define, `hasattr(obj, "thing")` was
False after a clean build, and `jm status --check` reported the project up to
date (the stub does match what jm intended to generate — see gh-622).

A *second* property landed correctly, because by then the array existed. So
the bug was precisely the zero-to-one boundary, in both halves of the pair:

* the binding — the array, its wrapper, and the `.tp_getset` slot pointing the
  type at it (an array no type references is inert dead code);
* the sacred `_core.h` — `_refresh_core_h_decls` ran only for standalone
  components, so the accessor prototype was missing and the freshly spliced
  binding called an undeclared function.

The end state matches what a standalone object has always done: jm declares
and binds, the user implements the accessor, and an unimplemented one is a
loud link error rather than a silently absent attribute.
"""

import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run

PROP = '\n[[reader.properties]]\nname = "thing"\ntype = "int"\n'


def _project(tmp_path, *, module, seed_property=False):
    root = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", root, [], [])
        if module:
            module_run(root, "wfm", ["reader"])
        object_run(
            root,
            "reader",
            "wfm" if module else None,
            state_vars=[("fs", "double", "0.0")],
        )
        if seed_property:
            property_run(
                root, "reader", "seed", "wfm" if module else None, "int", False
            )
    return root


def _apply(root, toml=PROP):
    m = root / "just-makeit.toml"
    m.write_text(m.read_text() + toml)
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(root)


def _fragment(root):
    return (root / "native" / "src" / "wfm" / "wfm_ext_reader.c").read_text()


def _header(root):
    return (root / "native" / "inc" / "reader" / "reader_core.h").read_text()


@pytest.fixture()
def first_property(tmp_path):
    """The reported case: an object with no properties gains one via TOML."""
    root = _project(tmp_path, module=True)
    assert "PyGetSetDef" not in _fragment(root), "precondition: no array yet"
    _apply(root)
    return root


class TestBinding:
    def test_getter_wrapper_spliced(self, first_property):
        assert "Reader_getprop_thing" in _fragment(first_property)

    def test_array_created(self, first_property):
        assert "PyGetSetDef" in _fragment(first_property)

    def test_type_object_points_at_the_array(self, first_property):
        """An array no PyTypeObject references is inert — it compiles and
        changes nothing, which would look fixed while still being broken."""
        frag = _fragment(first_property)
        slot = re.search(r"\.tp_getset\s*=\s*(\w+)\s*,", frag)
        assert slot, "no .tp_getset slot"
        assert re.search(
            rf"static\s+PyGetSetDef\s+{slot.group(1)}\s*\[", frag
        ), "tp_getset names an array the fragment does not define"

    def test_row_present(self, first_property):
        assert '"thing"' in _fragment(first_property)


class TestHeader:
    def test_accessor_declared(self, first_property):
        """Without this the spliced binding calls an undeclared function."""
        assert "reader_get_thing" in _header(first_property)


class TestIdempotence:
    def test_second_apply_changes_nothing(self, first_property):
        """Splicing must not duplicate the array, the row or the slot."""
        before = _fragment(first_property)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(first_property)
        after = _fragment(first_property)
        assert after == before
        assert after.count("static PyGetSetDef") == 1
        assert after.count(".tp_getset") == 1
        # One definition (`Reader_getprop_thing(ReaderObject *self, ...)`) and
        # one reference from the row — a duplicated definition would be a C
        # redefinition error, which is the failure mode splicing invites.
        assert after.count("Reader_getprop_thing(ReaderObject") == 1
        assert after.count('{ "thing"') == 1


class TestNoRegression:
    def test_second_property_still_splices(self, tmp_path):
        """gh-440's additive path — the case that already worked."""
        root = _project(tmp_path, module=True, seed_property=True)
        assert "PyGetSetDef" in _fragment(root)
        _apply(root)
        frag = _fragment(root)
        assert "Reader_getprop_thing" in frag
        assert frag.count("static PyGetSetDef") == 1

    def test_standalone_unaffected(self, tmp_path):
        """The standalone path never had this gap; it must keep working."""
        root = _project(tmp_path, module=False)
        _apply(root)
        ext = (root / "native" / "src" / "reader" / "reader_ext.c").read_text()
        assert "Reader_getprop_thing" in ext
        assert "reader_get_thing" in _header(root)
