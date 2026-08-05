"""gh-777 — a member that is simply *absent* is drift, not a tolerated diff.

gh-767 gave binding fragments their own `unreconciled` class, explicitly not
counted as drift, because the difference there is a wrapper **body** — the
author's, reformatted or hand-tuned, and not something any jm command clears.
That was right for the case it was built for and wrong for one it swallowed
by accident: a deleted `PyMethodDef`/`PyGetSetDef` row.

A missing row is not an edit jm should defer to. It is generated wiring that
has gone, `transplant_missing_bindings` already knows how to put it back, and
`jm apply` does. Tolerating it let a project carry a member its `.pyi`
advertises and its extension does not define — indefinitely, with CI green.
That is the shape gh-622 and gh-767 were filed to end, and how doppler
accumulated 58 arity mismatches without a red build.

So the bucket now answers two questions instead of one:

* a declared member is **absent**  -> reconcilable, counts as drift;
* a **body** differs               -> the author's, tolerated as before.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _docsync as D  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _project(root: Path) -> Path:
    new_run("proj", root, fragments=True)
    module_run(root, "dsp")
    object_run(root, "fir", "dsp", state_vars=[("gain", "double", "1.0")])
    apply_run(root)
    return root / "native" / "src" / "dsp" / "dsp_ext_fir.c"


def _check(root: Path) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = _status.run(root, check=True)
    return rc, buf.getvalue()


class TestTheSplit:
    def test_a_deleted_row_is_drift(self, tmp_path, capsys):
        root = tmp_path / "proj"
        frag = _project(root)
        capsys.readouterr()
        assert _check(root)[0] == 0, "fixture must start clean"

        text = frag.read_text()
        row = re.search(r'\n\s*\{\s*"reset".*?\},', text, re.S)
        assert row, "fixture must contain the row it deletes"
        frag.write_text(text.replace(row.group(0), "", 1))

        rc, out = _check(root)
        assert rc != 0, "a deleted member must fail --check"
        assert "stale" in out

    def test_a_reformatted_body_is_still_tolerated(self, tmp_path, capsys):
        """The case the bucket exists for. Formatting is the author's and no
        jm command clears it, so it must not start failing the gate — that
        would be gh-775 again."""
        root = tmp_path / "proj"
        frag = _project(root)
        capsys.readouterr()

        frag.write_text(
            re.sub(r"^(\w+) ?\(", r"\1 (", frag.read_text(), flags=re.M)
        )
        rc, out = _check(root)
        assert rc == 0, out
        assert "unreconciled" in out


class TestTheComparison:
    REF = """\
static PyMethodDef Obj_methods[] = {
  {"step", (PyCFunction)Obj_step, METH_VARARGS, "step"},
  {"reset", (PyCFunction)Obj_reset, METH_NOARGS, "reset"},
  {NULL}
};
static PyGetSetDef Obj_getset[] = {
  {"gain", (getter)Obj_get_gain, NULL, "gain", NULL},
  {NULL}
};
"""

    def test_it_names_what_is_missing(self):
        existing = self.REF.replace(
            '  {"reset", (PyCFunction)Obj_reset, METH_NOARGS, "reset"},\n', ""
        )
        assert D.absent_members(existing, self.REF) == ["reset"]

    def test_a_missing_property_counts_too(self):
        existing = self.REF.replace(
            '  {"gain", (getter)Obj_get_gain, NULL, "gain", NULL},\n', ""
        )
        assert D.absent_members(existing, self.REF) == ["gain"]

    def test_identical_arrays_report_nothing(self):
        assert D.absent_members(self.REF, self.REF) == []

    def test_a_differing_body_reports_nothing(self):
        """Names only. Comparing anything about the bodies would drag the
        tolerated case back into the drift count."""
        formatted = re.sub(r"\(PyCFunction\)", "(PyCFunction) ", self.REF)
        assert D.absent_members(formatted, self.REF) == []

    def test_an_extra_member_is_not_reported(self):
        """A hand-added binding the reference does not generate is gh-770's
        business and explicitly preserved — it must not read as drift."""
        existing = self.REF.replace(
            "  {NULL}\n};\nstatic PyGetSetDef",
            '  {"hand", (PyCFunction)Obj_hand, METH_VARARGS, "h"},\n'
            "  {NULL}\n};\nstatic PyGetSetDef",
            1,
        )
        assert D.absent_members(existing, self.REF) == []
