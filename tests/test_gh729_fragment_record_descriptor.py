"""gh-729: an incrementally-added record method brings its structseq statics.

A sacred ``_ext_<obj>.c`` fragment only ever *gains* members on apply, and
:func:`_docsync.transplant_missing_bindings` does that by splicing two things:
the ``PyMethodDef`` row, and the wrapper function located **by name**.

A ``single = true`` record method needs a third thing — the file-scope
``PyStructSequence_Field`` / ``_Desc`` / ``PyTypeObject *`` statics that
:func:`_record.descriptor_c` emits. A full render prepends them to the wrapper
so the two always travel together; the incremental path saw a function and a
row and nothing else, so the fragment gained a body referencing three
undeclared symbols:

    error: 'Capture_summary_type' undeclared
    error: 'Capture_summary_desc' undeclared

Reproducing it needs the *manifest* edited directly, then ``jm apply`` —
``jm method`` regenerates the fragment wholesale, which emits the descriptor
and hides the bug. That is why the first reproduction attempt passed.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _record as R  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


@pytest.fixture
def spliced(tmp_path: Path):
    """A fragment that already exists, then gains a record method."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "cap")
    _quiet(
        object_run,
        root,
        "capture",
        "cap",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _quiet(apply_run, root)  # the fragment now exists and is sacred

    # Declaring the method in the MANIFEST is what forces the incremental
    # path. `jm method` re-renders the fragment and would mask this entirely.
    cfg = C.load(root)
    cfg["capture"].setdefault("methods", []).append(
        {
            "name": "summary",
            "arg_type": "float[]",
            "return_type": "cap_sum_t",
            "single": True,
            "record_name": "Summary",
            "result_fields": [
                {"name": "peak", "type": "double", "doc": "Peak level, dBFS."}
            ],
        }
    )
    C.save(root, cfg)
    _quiet(apply_run, root)
    frag = next((root / "native/src/cap").glob("*_ext_*capture*.c"))
    return root, frag


class TestDescriptorIsSpliced:
    def test_the_wrapper_and_row_still_arrive(self, spliced):
        """The behaviour that already worked must keep working."""
        text = spliced[1].read_text(encoding="utf-8")
        assert "_summary(" in text
        assert '"summary"' in text

    @pytest.mark.parametrize(
        "symbol",
        ["_summary_fields[]", "_summary_desc", "_summary_type = NULL"],
        ids=["fields", "desc", "type"],
    )
    def test_each_static_is_declared(self, spliced, symbol):
        assert symbol in spliced[1].read_text(encoding="utf-8")

    def test_no_symbol_is_referenced_undeclared(self, spliced):
        """The reported symptom, asserted as the invariant behind it."""
        text = spliced[1].read_text(encoding="utf-8")
        for sym in ("_summary_type", "_summary_desc"):
            assert text.count(sym) >= 2, (
                f"{sym} is referenced but never declared — the fragment will "
                "not compile"
            )

    def test_the_descriptor_precedes_the_body(self, spliced):
        """C needs the declaration first; splice order is not cosmetic."""
        text = spliced[1].read_text(encoding="utf-8")
        assert text.index("PyStructSequence_Field") < text.index("_summary(")

    def test_the_field_doc_rides_along(self, spliced):
        """gh-646's docs must survive the incremental path too."""
        assert "Peak level, dBFS." in spliced[1].read_text(encoding="utf-8")

    def test_a_second_apply_does_not_duplicate_it(self, spliced):
        """A duplicated static is a redefinition error, not a cosmetic wart."""
        root, frag = spliced
        _quiet(apply_run, root)
        text = frag.read_text(encoding="utf-8")
        assert text.count("_summary_fields[]") == 1
        assert text.count("_summary_type = NULL") == 1

    def test_apply_is_a_fixed_point(self, spliced):
        root, frag = spliced
        before = frag.read_text(encoding="utf-8")
        _quiet(apply_run, root)
        assert frag.read_text(encoding="utf-8") == before


class TestFindDescriptor:
    """The locator lives next to the emitter so the two cannot drift."""

    def test_it_finds_what_descriptor_c_emits(self):
        flds = [R.RecordField("peak", "double", "Peak.")]
        emitted = R.descriptor_c("Obj_m", "mod.Rec", "Doc.", flds)
        found = R.find_descriptor(
            f"int before(void);\n{emitted}\nint after;", "Obj_m"
        )
        # Compared stripped: the emitter ends its block with a blank line for
        # spacing, and whether the locator swallows that is not the contract.
        # What matters is that every declaration comes back.
        assert found.strip() == emitted.strip()

    def test_it_is_empty_when_absent(self):
        assert R.find_descriptor("static int x = 0;\n", "Obj_m") == ""

    def test_it_does_not_match_a_different_method(self):
        flds = [R.RecordField("peak", "double", "")]
        emitted = R.descriptor_c("Obj_other", "mod.Rec", "Doc.", flds)
        assert R.find_descriptor(emitted, "Obj_m") == ""
