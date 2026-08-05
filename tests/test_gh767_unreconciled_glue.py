"""gh-767: ``status`` can see glue that ``apply`` renders but never rewrites.

``_apply._sync_missing`` copies a rendered file back only when the real tree
*lacks* it. Everything else is reconciled by ``_apply._sync_aggregates``, whose
set is hand-enumerated — root CMakeLists, umbrella header, package
``__init__.py``, and each module's ``__init__.py`` / ``ext.c`` / ``CMakeLists``
/ ``.pyi``. The per-object binding fragments (``<mod>_ext_<obj>.c``) were never
added to it.

That made them invisible twice over. ``apply`` leaves them frozen at whatever
jm emitted when they were first created, and ``status`` could not see it
either: it copies the real tree into its scratch, ``apply`` rewrites neither
side, and identical stale bytes compare equal. **The comparison was working
correctly on two inputs that were both wrong** — a blind spot rather than a
false negative.

Measured on doppler: 69 fragments reported, every one carrying real content
the generator has since changed (the regenerated `agc` binding adds a
`PyArray_IS_C_CONTIGUOUS` check the committed one lacks). It showed up for
doppler independently as 58 bindings whose Python-level arity no longer
matched the generator (gh-761) and 45 doctest lines divergent from the `.pyi`
beside them — with ``status --check`` reporting clean throughout.

These are reported but **not counted as drift**, deliberately: ``jm apply``
will not fix them, so failing ``--check`` would turn every existing project's
CI red for something no jm command clears. That is the gh-752 precedent —
report the count, let a project opt into strictness separately.
"""

from __future__ import annotations

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _module_project(root: Path) -> Path:
    """A two-object module, and the path of one object's binding fragment."""
    new_run("proj", root, fragments=True)
    module_run(root, "dsp")
    for name in ("fir", "biquad"):
        object_run(root, name, "dsp", state_vars=[("gain", "double", "1.0")])
    apply_run(root)
    return root / "native" / "src" / "dsp" / "dsp_ext_fir.c"


def _report(root: Path, **kw) -> tuple[int, str]:
    buf = io.StringIO()
    with (
        contextlib.redirect_stdout(buf),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        rc = _status.run(root, **kw)
    return rc, buf.getvalue()


class TestTheBlindSpot:
    """A stale fragment is seen, and named for what it is."""

    def test_a_clean_project_reports_nothing_unreconciled(self, tmp_path):
        root = tmp_path / "proj"
        _module_project(root)
        rc, out = _report(root)
        assert rc == 0
        assert "UNRECONCILED" not in out

    def test_an_edited_fragment_is_reported(self, tmp_path):
        """The case that was invisible: the file differs from what jm would
        emit, and `apply` leaves it alone on both sides of the comparison."""
        root = tmp_path / "proj"
        frag = _module_project(root)
        frag.write_text(
            frag.read_text().replace("PyObject", "PyObject /* edited */", 1)
        )

        # apply genuinely does not fix it — that is the premise.
        apply_run(root)
        assert "/* edited */" in frag.read_text()

        rc, out = _report(root)
        assert "UNRECONCILED (1)" in out
        assert "dsp_ext_fir.c" in out

    def test_it_is_reported_but_not_counted_as_drift(self, tmp_path):
        """`jm apply` cannot clear it, so `--check` must not fail on it.

        Counting it would turn every existing project's CI red for something
        no jm command fixes — the gh-752 precedent applies.
        """
        root = tmp_path / "proj"
        frag = _module_project(root)
        frag.write_text(frag.read_text().replace("PyObject", "PyObject ", 1))

        rc, _ = _report(root)
        assert rc == 0

    def test_check_still_mentions_it(self, tmp_path):
        """Under --check the per-file list is suppressed, but silence here is
        what let 58 of doppler's fragments hide behind "OK — up to date"."""
        root = tmp_path / "proj"
        frag = _module_project(root)
        frag.write_text(frag.read_text().replace("PyObject", "PyObject ", 1))

        rc, out = _report(root, check=True)
        assert rc == 0
        assert "unreconciled" in out.lower()

    def test_the_ok_line_is_qualified(self, tmp_path):
        """ "OK — up to date" must not be said over files the generator no
        longer agrees with."""
        root = tmp_path / "proj"
        frag = _module_project(root)
        frag.write_text(frag.read_text().replace("PyObject", "PyObject ", 1))

        _, out = _report(root)
        assert "OK — up to date" in out
        assert "unreconciled" in out

    def test_the_wording_does_not_promise_apply_will_fix_it(self, tmp_path):
        root = tmp_path / "proj"
        frag = _module_project(root)
        frag.write_text(frag.read_text().replace("PyObject", "PyObject ", 1))

        _, out = _report(root)
        section = out.split("UNRECONCILED")[1]
        # gh-770 changed what is true here: apply now reconciles these
        # member by member, so the old blanket "will NOT rewrite" would be a
        # lie. What must still not be promised is a *full* sync — the wrapper
        # body is never re-rendered, and that is the whole residue.
        flat = " ".join(section.split())  # the text is hard-wrapped
        assert "will not do is re-render a wrapper body" in flat
        assert "will NOT rewrite" not in flat
        # The STALE section's promise must not be attached to these.
        assert "Run `jm apply` to sync" not in section.split("summary:")[0]

    def test_the_real_tree_is_never_touched(self, tmp_path):
        """status deletes the fragment from its *scratch* only."""
        root = tmp_path / "proj"
        frag = _module_project(root)
        frag.write_text(frag.read_text().replace("PyObject", "PyObject ", 1))
        before = frag.read_bytes()

        _report(root)

        assert frag.read_bytes() == before


class TestTheCandidateSet:
    """Derived from the manifest, so a new module or object is covered."""

    def test_derived_from_the_manifest_not_a_glob(self, tmp_path):
        root = tmp_path / "proj"
        _module_project(root)
        found = _status._unreconciled_glue(root, C.load(root))
        assert found == {
            "native/src/dsp/dsp_ext_fir.c",
            "native/src/dsp/dsp_ext_biquad.c",
        }

    def test_an_object_added_later_is_covered_without_edits(self, tmp_path):
        root = tmp_path / "proj"
        _module_project(root)
        object_run(root, "iir", "dsp", state_vars=[("q", "double", "1.0")])
        apply_run(root)

        found = _status._unreconciled_glue(root, C.load(root))
        assert "native/src/dsp/dsp_ext_iir.c" in found

    def test_hand_written_extra_fragments_are_excluded(self, tmp_path):
        """``<mod>_ext_<obj>_extra.c`` is the author's by contract (gh-543).

        It must never be deleted from the scratch, or `status` would report
        the author's own file as generated glue that disagrees with jm.
        """
        root = tmp_path / "proj"
        _module_project(root)
        extra = root / "native" / "src" / "dsp" / "dsp_ext_fir_extra.c"
        extra.write_text("/* hand-written */\n")

        found = _status._unreconciled_glue(root, C.load(root))
        assert not any(f.endswith("_extra.c") for f in found)

    def test_a_standalone_project_has_no_candidates(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, fragments=True)
        object_run(root, "solo", None, state_vars=[("g", "double", "1.0")])
        apply_run(root)

        assert _status._unreconciled_glue(root, C.load(root)) == set()


class TestAViewOwnsAFragmentToo:
    """gh-775, a 0.46.0 regression, and the failure was backwards from what
    "missing from the candidate set" sounds like.

    Views were not merely unreported. A view fragment stayed in the *compared*
    set while its object siblings were excluded, so a project that formats its
    generated C to a house style — which is the normal case, and the whole
    reason the unreconciled class exists — left the views, and only the views,
    failing `--check`. doppler's gate went green -> 1 stale on bytes that had
    not changed between releases.

    A view's fragment id is its lowercased `class_name`, not the parent
    component it shares (gh-504), and `_object._view_frag_id` owns that
    derivation. Enumerating fragments here under a *different* rule than the
    one naming them is how the gap opened, so this asks that function.
    """

    def _project_with_a_view(self, root: Path) -> Path:
        from just_makeit._view import run as view_run

        new_run("proj", root, fragments=True)
        module_run(root, "dsp")
        object_run(root, "ddcr", "dsp", state_vars=[("gain", "double", "1.0")])
        view_run(
            root, "ddcr", "MatchedDdcr", "dsp", create_fn="ddcr_create_matched"
        )
        apply_run(root)
        return root / "native" / "src" / "dsp" / "dsp_ext_matchedddcr.c"

    def test_the_view_fragment_is_a_candidate(self, tmp_path, capsys):
        root = tmp_path / "proj"
        frag = self._project_with_a_view(root)
        capsys.readouterr()
        assert frag.is_file(), "fixture must actually produce a view fragment"

        found = _status._unreconciled_glue(root, C.load(root))
        assert "native/src/dsp/dsp_ext_matchedddcr.c" in found
        # The object beside it was already covered; both, or the set is
        # inconsistent in a way that shows up as exactly this bug.
        assert "native/src/dsp/dsp_ext_ddcr.c" in found

    def test_it_uses_the_owner_of_the_frag_id_derivation(self):
        """A second `.lower()` here would drift from `_view_frag_id` the next
        time the naming changes — which is the shape of the original gap."""
        from just_makeit._object import _view_frag_id

        assert _view_frag_id({"class_name": "MatchedDdcr"}) == "matchedddcr"

    def test_the_view_is_classified_unreconciled_not_stale(
        self, tmp_path, capsys
    ):
        """The classification is the whole fix, and it is what a minimal
        project can actually show.

        An end-to-end "format it and watch --check stay green" test was
        written here first and **passed with the fix reverted**, so it is
        gone rather than kept for looking thorough. In a small project the
        sabotaged path is green for the *old* reason (gh-767's blind spot:
        neither side is rewritten, so identical stale bytes compare equal) —
        the two sides only diverge on a tree where apply has doc slots to
        refresh, which is doppler's case and not a fixture's. Asserting the
        set membership tests the thing that changed; asserting the gate
        tested a coincidence.
        """
        root = tmp_path / "proj"
        frag = self._project_with_a_view(root)
        capsys.readouterr()

        _, out = _report(root)
        rel = "native/src/dsp/dsp_ext_matchedddcr.c"
        assert rel in _status._unreconciled_glue(root, C.load(root))
        assert frag.is_file()
        # And it must not be reported as drift under either name.
        assert (
            "STALE" not in out.split("UNRECONCILED")[0]
            or rel not in out.split("UNRECONCILED")[0]
        )
