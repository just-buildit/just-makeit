"""gh-815: a return-shape change reaches the .pyi but never the binding.

gh-622 gave `jm apply` a warning for a sacred fragment whose member no longer
matches the manifest — but its fingerprint is the *calling convention*: the
`METH_*` flags and the `PyArg_Parse*` format. `status_return`, `error_negative`,
`single` and `record_dtype` all leave both of those identical and change what
comes back, so they sailed straight past it while the `.pyi`, regenerated from
the same manifest, moved.

The reported case shipped in doppler: `ber_meter.set_truth`'s manifest `doc`
said *"Raises ValueError if any index is outside 0..m-1"*, its C returned
`DP_OK`/`DP_ERR_INVALID`, and the binding returned `0` to Python and raised
nothing. Its own header doctest recorded the wrong behaviour as expected
output. Two faces, a docstring and a doctest all disagreeing with the manifest,
with no gate anywhere reporting it.

The check is deliberately **one-directional**: a shape the manifest now implies
and the fragment lacks is drift; a construct the fragment has and the reference
does not is a hand-written body doing more than jm would, which is the entire
point of a sacred fragment and must never warn.
"""

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._docsync import (
    _method_return_shapes,
    _raises,
    warn_signature_drift,
)
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

INT_METHOD = (
    '\n[[reader.methods]]\nname = "install"\n'
    'arg_type = "void"\nreturn_type = "int"\n'
)
STATUS_METHOD = INT_METHOD + "status_return = true\n"


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", root, [], [])
        module_run(root, "wfm", ["reader"])
        object_run(root, "reader", "wfm", state_vars=[("fs", "double", "0.0")])
    return root


def _apply(root, toml=None):
    if toml is not None:
        m = root / "just-makeit.toml"
        base = m.read_text().split("\n[[reader.methods]]")[0]
        m.write_text(base + toml)
    err = io.StringIO()
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(err),
    ):
        apply_run(root)
    return [
        ln for ln in err.getvalue().splitlines() if ln.startswith("warning:")
    ]


def _fragment(root):
    return root / "native" / "src" / "wfm" / "wfm_ext_reader.c"


class TestTheReportedDivergence:
    """The exact reproducer from the issue."""

    def test_adding_status_return_to_an_existing_member_is_reported(
        self, project
    ):
        _apply(project, INT_METHOD)
        frag = _fragment(project)
        assert "PyLong_FromLong" in frag.read_text()

        warnings = _apply(project, STATUS_METHOD)

        assert warnings, "the return shape changed and nothing said so"
        assert "install" in warnings[0]
        assert "Py_RETURN_NONE" in warnings[0]

    def test_the_binding_really_did_not_move(self, project):
        """The premise: apply only ever ADDS members, so the old binding
        stays. Without that there would be nothing to report.

        Scoped to the wrapper, not the whole file: the PyMethodDef *doc* slot
        is transplanted on every apply by design, so the file legitimately
        changes while the code that decides the return value does not.
        """

        def _wrapper(root):
            text = _fragment(root).read_text()
            i = text.index("Reader_install(")
            return text[i : text.index("\n}", i)]

        _apply(project, INT_METHOD)
        before = _wrapper(project)
        assert "PyLong_FromLong" in before
        _apply(project, STATUS_METHOD)
        assert _wrapper(project) == before

    def test_the_stub_moved_while_the_binding_did_not(self, project):
        """The two faces disagreeing is the actual defect."""
        _apply(project, INT_METHOD)
        _apply(project, STATUS_METHOD)
        pyi = (project / "src" / "dsp" / "wfm" / "wfm.pyi").read_text()
        assert "def install(self) -> None:" in pyi
        assert "PyLong_FromLong" in _fragment(project).read_text()

    def test_delete_and_reapply_clears_it(self, project):
        """The documented fix must actually work — and stay quiet after."""
        _apply(project, INT_METHOD)
        _apply(project, STATUS_METHOD)
        _fragment(project).unlink()
        assert _apply(project) == []
        assert _apply(project) == [], "a second apply re-reported it"

    def test_the_message_names_file_member_and_what_is_missing(self, project):
        _apply(project, INT_METHOD)
        (warning,) = _apply(project, STATUS_METHOD)
        assert "wfm_ext_reader.c" in warning
        assert "install:" in warning
        assert "absent here" in warning
        assert "just-makeit apply" in warning


class TestOnlyTheManifestDirection:
    """A fragment richer than the reference is never drift."""

    def test_a_marker_the_reference_lacks_is_not_reported(self, project):
        """A hand-written body that raises where the manifest does not ask it
        to is the author's business — warning would train them to ignore the
        channel."""
        _apply(project, INT_METHOD)
        frag = _fragment(project)
        text = frag.read_text()
        hand = text.replace(
            "int y = reader_install(self->handle);",
            "int y = reader_install(self->handle);\n"
            '    if (y == 7) { PyErr_Format(PyExc_ValueError, "no"); '
            "return NULL; }",
        )
        assert hand != text, "sanity: the edit applied"
        frag.write_text(hand)
        assert _apply(project) == []

    def test_an_unrelated_body_edit_is_not_drift(self, project):
        _apply(project, INT_METHOD)
        frag = _fragment(project)
        text = frag.read_text()
        frag.write_text(
            text.replace(
                "int y = reader_install", "int y = 2 * reader_install"
            )
        )
        assert _apply(project) == []


#: `_extract_c_function_bodies` matches this project's clang-format house
#: style only — return type on its own line, `name(` on the next. A one-line
#: definition is deliberately never extracted, so synthetic C written the
#: short way makes every assertion below pass vacuously.
_ROWS = (
    "static PyMethodDef X_methods[] = {\n"
    '  {"f", (PyCFunction)f, METH_NOARGS, "d"},\n'
    "  {NULL}\n};\n"
)


def _fn(body: str) -> str:
    return f"static PyObject *\nf(PyObject *self)\n{{\n{body}\n}}\n" + _ROWS


class TestEquivalentSpellings:
    """The fragment may be hand-written, so a shape has more than one form."""

    def test_the_fixture_parses_at_all(self):
        """Guard: every other test here is vacuous if this one fails."""
        assert _method_return_shapes(_fn("    Py_RETURN_NONE;"))["f"] == {
            "Py_RETURN_NONE"
        }

    def test_incref_py_none_satisfies_the_none_marker(self):
        """`Py_RETURN_NONE` is a macro; a body doing it by hand is the same
        shape and must not be flagged."""
        macro = _method_return_shapes(_fn("    Py_RETURN_NONE;"))["f"]
        manual = _method_return_shapes(
            _fn("    Py_INCREF(Py_None);\n    return Py_None;")
        )["f"]
        assert manual == macro == {"Py_RETURN_NONE"}

    def test_the_liveness_guard_alone_is_not_a_raise(self):
        """Every wrapper carries one `PyErr_SetString` for the destroyed
        guard. Counting it as error translation would match everything and
        neuter the axis."""
        guard = (
            "    if (!self->handle) { PyErr_SetString(PyExc_RuntimeError, "
            '"destroyed"); return NULL; }\n'
        )
        assert not _raises(guard)
        assert _raises(guard + '    PyErr_SetString(PyExc_ValueError, "x");')
        assert _raises(guard + '    PyErr_Format(PyExc_ValueError, "x");')

    def test_a_marker_named_in_a_docstring_does_not_count(self):
        """Comment and string contents are masked out."""
        text = _fn(
            "    /* returns Py_RETURN_NONE one day */\n"
            '    const char *d = "Py_RETURN_NONE";\n'
            "    return PyLong_FromLong(0);"
        )
        assert _method_return_shapes(text)["f"] == frozenset()


class TestBothAxesInOneReport:
    def test_a_signature_change_still_reports(self, project):
        """gh-622's axis must keep working — this extends it, not replaces."""
        _apply(project, INT_METHOD)
        frag = _fragment(project)
        text = frag.read_text()
        # Narrow the row to METH_NOARGS-with-args, a convention change.
        frag.write_text(text.replace("METH_NOARGS", "METH_VARARGS", 1))
        warnings = _apply(project)
        assert warnings and "binding" in warnings[0]

    def test_agreeing_fragments_report_nothing(self, project):
        assert _apply(project, INT_METHOD) == []
        assert _apply(project) == []


class TestReturnsDriftedNames:
    def test_warn_returns_the_members_it_reported(self):
        old = _fn("    return PyLong_FromLong(0);")
        new = _fn("    Py_RETURN_NONE;")
        assert warn_signature_drift("f.c", old, new) == ["f"]
        assert warn_signature_drift("f.c", new, new) == []
        # ...and not the other way round: a fragment richer than the manifest
        # is the hand-written case, never drift.
        assert warn_signature_drift("f.c", new, old) == []
