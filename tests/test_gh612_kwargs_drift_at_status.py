"""gh-612 — `jm status` reports a constructor whose kwlist has drifted.

An object whose declaration jm cannot express (a 2-D array param, a
length-derived companion argument, an elements-per-sample factor) has its
binding fragment hand-owned in full. The reporter's point is that the cost of
that is not the one hand-written file — it is that the object silently stops
receiving every future codegen fix, and nothing says so.

Measured: gh-422 fixed constructor argument order on 2026-07-08.
`CorrDetector2D`'s fragment was hand-owned, so it kept the pre-422 order while
its `.pyi` — generated from the manifest — stated the new one. doppler 0.38.1
shipped with a type checker blessing the call that raises:

    >>> CorrDetector2D(ref, 1, 0, 3, "median")   # what the .pyi advertises
    TypeError: argument 2 must be str, not int

jm has been able to answer this since doppler#616 — `warn_init_kwargs_drift`
is the same comparison — but only ever asked it mid-refresh, and `status`
redirects that stderr into a sink. So for a fragment nobody refreshes, which
is precisely the hand-owned case, the question was never put.

These tests pin the comparison and the wiring separately: the wiring is what
was missing, and a test that only exercised `init_kwargs_drift` would have
passed before this change.
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

# A binding whose constructor takes (ref, mode, dwell) …
EXISTING = """\
static int
Detector_init(DetectorObject *self, PyObject *args, PyObject *kwds)
{
  static char *kwlist[] = {"ref", "mode", "dwell", NULL};
  if (!PyArg_ParseTupleAndKeywords(args, kwds, "OsI", kwlist, &ref, &mode,
                                   &dwell))
    return -1;
  return 0;
}
"""

# … against a manifest that now generates (ref, dwell, mode). Same names,
# same arity, different order — no member is added or lost, so every
# member-level audit passes and the object still imports.
REFERENCE = """\
static int
Detector_init(DetectorObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"ref", "dwell", "mode", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OIs", kwlist, &ref, &dwell,
                                     &mode))
        return -1;
    return 0;
}
"""


class TestTheComparison:
    def test_a_reorder_is_drift(self):
        added, removed, reordered, detail = D.init_kwargs_drift(
            EXISTING, REFERENCE
        )
        assert (added, removed) == ((), ())
        assert reordered is True
        assert "ref/mode/dwell -> ref/dwell/mode" in detail

    def test_a_removed_keyword_is_named(self):
        ref = REFERENCE.replace('"ref", "dwell", "mode"', '"ref", "dwell"')
        _, removed, _, detail = D.init_kwargs_drift(EXISTING, ref)
        assert removed == ("mode",)
        assert "no longer accepted: mode" in detail

    def test_agreement_is_silent(self):
        assert D.init_kwargs_drift(EXISTING, EXISTING)[3] == ""

    def test_reformatting_alone_is_not_drift(self):
        """The whole point of the unreconciled bucket is that these fragments
        have been through the project's own formatter. If indentation counted
        as drift, every formatted project would light up and the report would
        be worthless — so the comparison must be on the parsed kwlist, never
        on the text."""
        assert EXISTING != REFERENCE
        same = REFERENCE.replace(
            '"ref", "dwell", "mode"', '"ref", "mode", "dwell"'
        )
        assert same != EXISTING, "fixture must still differ as text"
        assert D.init_kwargs_drift(EXISTING, same)[3] == ""


def _project(root: Path) -> Path:
    new_run("proj", root, fragments=True)
    module_run(root, "dsp")
    object_run(
        root,
        "det",
        "dsp",
        state_vars=[("mode", "double", "1.0"), ("dwell", "double", "2.0")],
    )
    apply_run(root)
    return root / "native" / "src" / "dsp" / "dsp_ext_det.c"


def _status_output(root: Path, check: bool = True) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = _status.run(root, check=check)
    return rc, buf.getvalue()


def _reorder_kwlist(frag: Path) -> None:
    """Hand-edit the fragment's constructor to swap two keywords — the
    hand-owned edit gh-422 made to `CorrDetector2D` in reverse. Both spellings
    compile and neither adds or drops a member, so nothing else jm checks can
    see it."""
    text = frag.read_text()
    m = re.search(r"static char \*kwlist\[\] = \{([^}]*)\};", text)
    assert m, "fixture must have a kwlist to reorder"
    names = re.findall(r'"([^"]+)"', m.group(1))
    assert len(names) >= 2, f"need two keywords to swap, got {names}"
    swapped = m.group(0).replace(
        f'"{names[0]}", "{names[1]}"', f'"{names[1]}", "{names[0]}"', 1
    )
    assert swapped != m.group(0), "the swap must actually change the text"
    frag.write_text(text.replace(m.group(0), swapped, 1))


class TestTheWiring:
    """The comparison already existed and was already tested. What was
    missing is `status` asking it — so these drive the reporting path
    end-to-end. Testing `init_kwargs_drift` alone would have passed before
    this change and proved nothing.
    """

    def test_status_reports_a_reordered_constructor(self, tmp_path, capsys):
        root = tmp_path / "proj"
        frag = _project(root)
        capsys.readouterr()
        assert _status_output(root)[0] == 0, "fixture must start clean"
        assert "KWARGS" not in _status_output(root)[1], (
            "a clean project must not report kwargs drift"
        )

        _reorder_kwlist(frag)
        rc, out = _status_output(root)
        assert "KWARGS (1)" in out, out
        assert "dsp_ext_det.c" in out
        assert "new positional order" in out

    def test_it_is_reported_without_check_too(self, tmp_path, capsys):
        """`--check` collapses the listings; this section is one of the few
        that must print either way, like DROPPED and DRIFT."""
        root = tmp_path / "proj"
        frag = _project(root)
        capsys.readouterr()
        _reorder_kwlist(frag)
        assert "KWARGS (1)" in _status_output(root, check=False)[1]

    def test_it_fails_the_gate(self, tmp_path, capsys):
        """Reversed by gh-823, and the reversal is the point of that issue.

        gh-612 deliberately did NOT gate this, reasoning that "jm regenerates
        a kwlist only with the body it belongs to, so there is no command to
        run — failing `--check` would demand a fix jm cannot perform", and
        calling it the opposite of gh-777 where `apply` could put the member
        back.

        The premise turned out to be wrong. There *is* a command: delete the
        fragment and re-apply, which is the same remedy gh-815's return-shape
        warning already prescribes. It is destructive of hand-written bodies,
        which is why jm will not do it unasked — but that makes it the
        author's decision to take, not an impossible one. Confirmed on the
        reporting project: deleting the fragment and re-applying fixed the
        constructor outright, with an otherwise identical member set.

        What settled it is what happened while it was merely reported. The
        warning was correct, named the file and the reordering, and printed on
        every apply for months — inside a block of a dozen warnings about
        fragments that were fine. A public constructor shipped raising when
        called as documented, with a type checker endorsing the failing call.
        The distance between "jm knew" and "someone acted" is the exit code.

        Unconditional rather than opt-in: a project that never enables an
        opt-in never learns its signature is broken, and that silence is the
        whole failure mode. `status_allow` is the escape hatch — the check
        stays on and an instance is exempted by name.
        """
        root = tmp_path / "proj"
        frag = _project(root)
        capsys.readouterr()
        _reorder_kwlist(frag)
        rc, out = _status_output(root)
        assert rc != 0, "a drifted constructor must fail --check"
        assert "kwargs-drift (!)" in out
        assert "OK — up to date" not in out, (
            "and it must not open with OK: that headline is what a reader "
            "takes away, and it said fine while the signature was broken"
        )
