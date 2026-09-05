"""gh-1270: two records that alias but disagree on doc -- reported, not lost.

gh-1268 made two same-shape records under one public name SAFE: the second
is aliased to the first's type object (``B_sum_type = A_sum_type;``) instead
of registering a second one and freeing the first. What it deliberately did
not answer is what happens to the ALIASED record's own doc.

The alias means the aliased method's descriptor (`B_sum_desc`, with its own
``record_doc`` or ``--result-field`` doc) is compiled and then never used --
nothing ever calls ``PyStructSequence_NewType`` on it, so nothing it says
reaches ``help()`` or the ``.pyi``. A reader of the aliased method sees the
FIRST method's prose on both faces, and until this fix nothing said so:

    jm method a sum ... --record-doc "A's doc."
    jm method b sum ... --record-doc "B's doc."   # silently dropped

Two methods sharing one record almost always share its documentation, so
this is NOT a refusal the way a shape mismatch is (`_record.resolve` already
raises for that; a doc-only disagreement corrupts nothing the way aliasing a
mismatched shape would). It is `_record.doc_conflict`, an advisory
`_report.warn` naming both methods, both doc strings, and the two ways out
(`--record-name`, or dropping `--record-doc`) -- surfaced wherever a record's
runtime type is assembled: the module aggregator (`_object.py`, covering a
parent+view or two sibling objects sharing a name) and a standalone object's
own `PyInit_` (`_glue.py`, covering two methods on one object).
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._record import RecordReg, doc_conflict  # noqa: E402

_FIELDS = [{"name": "n", "type": "uint64_t"}]
_SHAPE = (("n", "uint64_t"),)


def _quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*a, **kw)
    return out.getvalue()


class TestDocConflict:
    """Unit-level: the pure function over a list of `RecordReg`."""

    def test_same_shape_different_doc_is_reported(self):
        a = RecordReg("A_sum", "Sum", _SHAPE, "A's doc.")
        b = RecordReg("B_sum", "Sum", _SHAPE, "B's doc.")
        msgs = doc_conflict([a, b])
        assert len(msgs) == 1
        assert "A_sum" in msgs[0] and "B_sum" in msgs[0]
        assert "A's doc." in msgs[0] and "B's doc." in msgs[0]
        assert "--record-name" in msgs[0]

    def test_same_shape_same_doc_is_silent(self):
        a = RecordReg("A_sum", "Sum", _SHAPE, "Shared doc.")
        b = RecordReg("B_sum", "Sum", _SHAPE, "Shared doc.")
        assert doc_conflict([a, b]) == []

    def test_two_undeclared_docs_derive_identically_and_are_silent(self):
        """Neither method declares `record_doc` -- both fall back to the same
        CPython-style synopsis, so there is nothing to report."""
        a = RecordReg("A_sum", "Sum", _SHAPE, "Sum(n)")
        b = RecordReg("B_sum", "Sum", _SHAPE, "Sum(n)")
        assert doc_conflict([a, b]) == []

    def test_a_shape_mismatch_is_not_this_functions_concern(self):
        """`resolve()` already refuses this pairing; doc_conflict must not
        ALSO flag it, or a single defect prints two different complaints."""
        a = RecordReg("A_sum", "Sum", _SHAPE, "A's doc.")
        b = RecordReg("B_sum", "Sum", (("q", "double"),), "B's doc.")
        assert doc_conflict([a, b]) == []

    def test_different_names_never_collide(self):
        a = RecordReg("A_m", "Sum", _SHAPE, "A's doc.")
        b = RecordReg("B_m", "Other", _SHAPE, "B's doc.")
        assert doc_conflict([a, b]) == []

    def test_three_way_only_flags_the_disagreeing_pair(self):
        a = RecordReg("A_m", "Sum", _SHAPE, "Shared.")
        b = RecordReg("B_m", "Sum", _SHAPE, "Shared.")
        c = RecordReg("C_m", "Sum", _SHAPE, "Different.")
        msgs = doc_conflict([a, b, c])
        assert len(msgs) == 1
        assert "A_m" in msgs[0] and "C_m" in msgs[0] and "B_m" not in msgs[0]


class TestRegistrationsCarriesTheDoc:
    """`_record.registrations` fills `RecordReg.doc` from the same source
    the `.pyi` and the C descriptor derive it from -- not a second opinion."""

    def test_a_declared_record_doc_is_carried(self):
        from just_makeit._record import registrations

        methods = [
            {
                "name": "sum",
                "single": True,
                "record_name": "Sum",
                "record_doc": "Custom doc.",
                "result_fields": _FIELDS,
            }
        ]
        (reg,) = registrations(methods, "A")
        assert reg.doc == "Custom doc."

    def test_an_undeclared_doc_falls_back_to_the_synopsis(self):
        from just_makeit._record import registrations

        methods = [
            {
                "name": "sum",
                "single": True,
                "record_name": "Sum",
                "result_fields": _FIELDS,
            }
        ]
        (reg,) = registrations(methods, "A")
        assert reg.doc == "Sum(n)"


def _two_module_objects(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "m")
    for obj in ("a", "b"):
        _quiet(
            object_run,
            root,
            obj,
            "m",
            state_vars=[("x", "double", "0.0")],
            arg_type="double",
            return_type="double",
        )
    return root


class TestModuleObjectsSharingARecord:
    """Two sibling objects in one module, same record_name and shape."""

    def test_declaring_the_second_prints_the_warning(self, tmp_path, capsys):
        root = _two_module_objects(tmp_path)
        _quiet(
            method_run,
            root,
            "a",
            "sum",
            "m",
            "void",
            "a_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            record_doc="A's doc.",
            result_fields=[dict(f) for f in _FIELDS],
        )
        capsys.readouterr()
        _quiet(
            method_run,
            root,
            "b",
            "sum",
            "m",
            "void",
            "b_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            record_doc="B's doc.",
            result_fields=[dict(f) for f in _FIELDS],
        )
        err = capsys.readouterr().err
        assert "warning" in err
        assert "A's doc." in err and "B's doc." in err
        assert "--record-name" in err

    def test_the_command_still_succeeds(self, tmp_path):
        """Advisory, not a refusal -- exit 0, files written."""
        root = _two_module_objects(tmp_path)
        _quiet(
            method_run,
            root,
            "a",
            "sum",
            "m",
            "void",
            "a_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            record_doc="A's doc.",
            result_fields=[dict(f) for f in _FIELDS],
        )
        _quiet(
            method_run,
            root,
            "b",
            "sum",
            "m",
            "void",
            "b_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            record_doc="B's doc.",
            result_fields=[dict(f) for f in _FIELDS],
        )
        agg = (root / "native" / "src" / "m" / "m_ext.c").read_text(
            encoding="utf-8"
        )
        assert "B_sum_type = A_sum_type;" in agg

    def test_agreeing_docs_print_no_warning(self, tmp_path, capsys):
        root = _two_module_objects(tmp_path)
        _quiet(
            method_run,
            root,
            "a",
            "sum",
            "m",
            "void",
            "a_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            result_fields=[dict(f) for f in _FIELDS],
        )
        capsys.readouterr()
        _quiet(
            method_run,
            root,
            "b",
            "sum",
            "m",
            "void",
            "b_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            result_fields=[dict(f) for f in _FIELDS],
        )
        assert "warning" not in capsys.readouterr().err

    def test_status_check_does_not_gate_on_it(self, tmp_path):
        """Advisory: `jm status --check` has nothing to fail on."""
        from just_makeit._status import run as status_run

        root = _two_module_objects(tmp_path)
        for obj, doc in (("a", "A's doc."), ("b", "B's doc.")):
            _quiet(
                method_run,
                root,
                obj,
                "sum",
                "m",
                "void",
                f"{obj}_sum_t",
                False,
                [],
                single=True,
                record_name="Sum",
                record_doc=doc,
                result_fields=[dict(f) for f in _FIELDS],
            )
        drift = status_run(root, check=True)
        assert drift == 0


class TestStandaloneObjectSharingARecord:
    """Two methods on ONE standalone object, same record_name and shape."""

    def test_declaring_the_second_prints_the_warning(self, tmp_path, capsys):
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "o",
            None,
            state_vars=[("x", "double", "0.0")],
            arg_type="double",
            return_type="double",
        )
        _quiet(
            method_run,
            root,
            "o",
            "m1",
            None,
            "void",
            "o_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            record_doc="First.",
            result_fields=[dict(f) for f in _FIELDS],
        )
        capsys.readouterr()
        _quiet(
            method_run,
            root,
            "o",
            "m2",
            None,
            "void",
            "o_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            record_doc="Second.",
            result_fields=[dict(f) for f in _FIELDS],
        )
        err = capsys.readouterr().err
        assert "warning" in err
        assert "First." in err and "Second." in err
