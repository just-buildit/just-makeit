"""gh-622: a signature change reaches the .pyi but never the sacred binding.

`jm apply` splices *missing* members into a sacred `_ext_<obj>.c` fragment
(gh-440) but never revises an existing one — its body is the user's. So when a
manifest edit or a jm upgrade changes a method's generated signature, the
`.pyi` takes the new one and the binding keeps the old, and nothing reports it:
`jm status --check` compares manifest-owned files, and both artifacts
legitimately match what jm would write.

The reporter hit this bumping doppler across a jm release that changed
`<verb>_max_out()`: 26 methods across 10 modules gained a parameter in the stub
with no binding change, found only by building and calling each one. The stub
said `execute_max_out(1024)` was valid (`TypeError` at runtime) and
`execute_max_out()` was not (works).

Re-rendering is not on the table here — the bodies are hand-owned — so this
reports what diverged, the same trade gh-609 made for a hand-edited `impl`
body. The signature fingerprint is deliberately narrow: the `METH_*` flags and
the `PyArg_Parse*` format string, both jm-generated. The body around them is
never compared, so a hand-written implementation does not read as drift — that
false positive would train users to ignore the warning, which is worse than
not having it.
"""

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._docsync import _method_signatures, warn_signature_drift
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

VOID_METHOD = (
    '\n[[reader.methods]]\nname = "zzz"\n'
    'arg_type = "void"\nreturn_type = "double"\n'
)
SCALAR_METHOD = (
    '\n[[reader.methods]]\nname = "zzz"\n'
    'arg_type = "float _Complex"\nreturn_type = "double"\n'
)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "dsp"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("dsp", root, [], [])
        module_run(root, "wfm", ["reader"])
        object_run(root, "reader", "wfm", state_vars=[("fs", "double", "0.0")])
    return root


def _apply(root, toml=None):
    """Apply, optionally after rewriting the manifest; return stderr warnings."""
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


class TestFingerprint:
    def test_flags_and_format_are_captured(self, project):
        _apply(project, SCALAR_METHOD)
        sigs = _method_signatures(_fragment(project).read_text())
        assert "zzz" in sigs
        flags, fmt = sigs["zzz"]
        assert "METH_VARARGS" in flags
        assert fmt, "the PyArg format string is the arity half of the pair"

    def test_body_is_not_part_of_the_fingerprint(self, project):
        """A hand-written implementation must never read as drift."""
        _apply(project, SCALAR_METHOD)
        frag = _fragment(project)
        text = frag.read_text()
        before = _method_signatures(text)
        hand = text.replace(
            "double y = reader_zzz", "double y = 42.0 * reader_zzz"
        )
        assert hand != text, "sanity: the body edit applied"
        assert _method_signatures(hand) == before


class TestWarning:
    def test_silent_on_first_declaration(self, project):
        assert _apply(project, VOID_METHOD) == []

    def test_silent_when_nothing_changed(self, project):
        _apply(project, VOID_METHOD)
        assert _apply(project) == []

    def test_fires_when_the_signature_changes(self, project):
        _apply(project, VOID_METHOD)
        warnings = _apply(project, SCALAR_METHOD)
        assert len(warnings) == 1
        w = warnings[0]
        assert "zzz" in w
        assert "METH_NOARGS" in w and "METH_VARARGS" in w
        # Actionable: name the file to delete, and the cost of deleting it.
        assert "wfm_ext_reader.c" in w
        assert "just-makeit apply" in w

    def test_body_edit_alone_does_not_warn(self, project):
        """The false positive that would train users to ignore the warning."""
        _apply(project, SCALAR_METHOD)
        frag = _fragment(project)
        frag.write_text(
            frag.read_text().replace(
                "double y = reader_zzz", "double y = 42.0 * reader_zzz"
            )
        )
        assert _apply(project) == []


class TestUnit:
    def test_returns_drifted_names(self, capsys):
        existing = (
            "static PyObject *\n"
            "Obj_go(ObjObject *self, PyObject *Py_UNUSED(ignored))\n"
            "{ return NULL; }\n"
            "static PyMethodDef Obj_methods[] = {\n"
            '    {"go", (PyCFunction)Obj_go, METH_NOARGS, "go"},\n'
            "    {NULL}\n};\n"
        )
        reference = (
            "static PyObject *\n"
            "Obj_go(ObjObject *self, PyObject *args)\n"
            '{ double x; if (!PyArg_ParseTuple(args, "d", &x)) return NULL;'
            " return NULL; }\n"
            "static PyMethodDef Obj_methods[] = {\n"
            '    {"go", (PyCFunction)Obj_go, METH_VARARGS, "go"},\n'
            "    {NULL}\n};\n"
        )
        assert warn_signature_drift("frag.c", existing, reference) == ["go"]
        assert "frag.c" in capsys.readouterr().err

    def test_identical_texts_are_quiet(self, capsys):
        text = (
            "static PyMethodDef Obj_methods[] = {\n"
            '    {"go", (PyCFunction)Obj_go, METH_NOARGS, "go"},\n'
            "    {NULL}\n};\n"
        )
        assert warn_signature_drift("frag.c", text, text) == []
        assert capsys.readouterr().err == ""
