"""gh-1192: `status` stops calling an apply-fixable fragment permanent.

`status` split UNRECONCILED into two halves (gh-848), which was right — one
list made the actionable ones invisible. The split is driven by
`_docsync.signature_drift_details`, so **only a signature difference produces a
reason**, and everything else falls to AUTHOR-OWNED by default:

    AUTHOR-OWNED (1) — these differ because you wrote them that way.
    Nothing to do; they stay unreconciled permanently.

A doc-slot difference is not a signature difference, so it landed there — while
being exactly the kind `apply` reconciles in place. Both sentences were false
for it: the author did not write the difference, and the next `apply` clears it.

The default was also the wrong way round. An unexplained difference was
reported as *definitely the author's*, which is the confident reading of "we do
not know".

Why status could not already tell
---------------------------------
gh-767 has `status` DELETE these fragments from its scratch so a fresh render
materializes and the comparison can see them at all. That is what makes the
in-place path the one thing its own replay never exercises — so "would apply
change this file?" was a question it had no way to ask.

It asks the code that does the changing:
`refresh_module_fragment_docs(dry_run=True)` computes without writing and
returns what it would have written. One implementation, so the report cannot
disagree with the behaviour it describes.

Measured, not assumed
---------------------
On doppler at 0.71.1: of 87 fragments, **30** are ones `apply` rewrites in
place — 24 doc-only, 6 structural. Every one of them was being reported as
permanent.

(The first measurement said 75, from a fixture that copied `native/` and the
central manifest but not `objects/` — doppler is split-layout, so the cfg had
no object declarations and every fragment looked wrong. The number that mattered
was the one produced by a correct fixture.)

Deliberately NOT widened
------------------------
`--check` still ignores unreconciled files. Making 30 of them fail on a
downstream that has been green is a separate decision with a measured cost, and
this change is about the report telling the truth.
`TestTheGateIsUnchanged` holds that line so it stays a decision rather than a
drift.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

HEADER_DOC = "HEADERDOC marker."


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


def _frag(root: Path) -> Path:
    return root / "native" / "src" / "m" / "m_ext_o.c"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A module object with a property, applied and clean."""
    assert _cli("new", "r", cwd=tmp_path).returncode == 0
    root = tmp_path / "r"
    for step in (
        ("module", "m"),
        ("object", "o", "--module", "m", "--state", "g:double:1.0"),
        ("property", "o", "level", "--module", "m", "--type", "double"),
    ):
        out = _cli(*step, cwd=root)
        assert out.returncode == 0, f"{step}: {out.stdout}{out.stderr}"
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


def _add_header_doc(root: Path) -> None:
    """Document a getter in the sacred header — the plainest in-place
    refresh there is, and the one that reproduces gh-1192 with no manifest
    `doc` involved at all (so it is not gh-1191 wearing a different hat)."""
    hdr = root / "native" / "inc" / "o" / "o_core.h"
    body = hdr.read_text(encoding="utf-8")
    decl = "double o_get_level(const o_state_t *state);"
    assert decl in body, body
    hdr.write_text(
        body.replace(decl, f"/**\n * @brief {HEADER_DOC}\n */\n" + decl, 1),
        encoding="utf-8",
    )


def _hand_edit_a_body(root: Path) -> None:
    """Change a wrapper BODY, which apply will not re-render (gh-770). The
    control: this one really is the author's and really is permanent."""
    frag = _frag(root)
    body = frag.read_text(encoding="utf-8")
    new, n = re.subn(
        r"(\n    o_reset\(self->handle\);)",
        r"\1\n    /* HAND EDIT */",
        body,
        count=1,
    )
    assert n == 1, body
    frag.write_text(new, encoding="utf-8")


class TestAnApplyFixableFragmentSaysSo:
    def test_it_is_not_reported_as_permanent(self, project: Path) -> None:
        _add_header_doc(project)
        out = _cli("status", cwd=project).stdout
        assert "APPLY FIXES THESE (1)" in out, out
        assert "stay unreconciled permanently" not in out, out

    def test_the_remedy_named_is_the_one_that_works(
        self, project: Path
    ) -> None:
        """`ACTIONABLE`'s remedy is "delete the file and re-run apply", which
        loses a hand-written body. For this half that would be advice with a
        cost and no reason."""
        _add_header_doc(project)
        out = _cli("status", cwd=project).stdout
        section = out[out.index("APPLY FIXES THESE") :]
        assert "Run `jm apply`" in section, section
        assert "Delete the file" not in section.split("AUTHOR-OWNED")[0]

    def test_the_fragment_is_named(self, project: Path) -> None:
        _add_header_doc(project)
        out = _cli("status", cwd=project).stdout
        assert "native/src/m/m_ext_o.c" in out, out

    def test_apply_clears_it(self, project: Path) -> None:
        """The whole claim. A finding that says "run apply" and is still there
        afterwards is the shape this repo has paid for before."""
        _add_header_doc(project)
        assert "APPLY FIXES THESE" in _cli("status", cwd=project).stdout
        assert _cli("apply", cwd=project).returncode == 0
        after = _cli("status", cwd=project).stdout
        assert "APPLY FIXES THESE" not in after, after
        assert HEADER_DOC in _frag(project).read_text(encoding="utf-8")


class TestWhatIsStillAuthorOwned:
    """The other half has to keep working, and its text has to stay true —
    the point is not to empty the bucket, it is to stop it swallowing files
    that do not belong in it."""

    def test_a_hand_edited_body_is_still_permanent(
        self, project: Path
    ) -> None:
        _hand_edit_a_body(project)
        out = _cli("status", cwd=project).stdout
        assert "AUTHOR-OWNED (1)" in out, out
        assert "APPLY FIXES THESE" not in out, out

    def test_and_apply_really_does_leave_it(self, project: Path) -> None:
        """AUTHOR-OWNED now claims something checkable, so check it."""
        _hand_edit_a_body(project)
        assert _cli("apply", cwd=project).returncode == 0
        assert "/* HAND EDIT */" in _frag(project).read_text(encoding="utf-8")
        assert "AUTHOR-OWNED" in _cli("status", cwd=project).stdout

    def test_both_at_once_are_split(self, project: Path) -> None:
        """One fragment can only land in one bucket, so the split is shown
        with the two causes on the same file: a hand-edited body makes it
        author-owned even though a doc refresh is also pending. The bucket
        that wins is the honest one — `apply` will not fully reconcile it."""
        _add_header_doc(project)
        _hand_edit_a_body(project)
        out = _cli("status", cwd=project).stdout
        assert "UNRECONCILED (1)" in out, out
        assert "APPLY FIXES THESE (1)" in out, out


class TestTheGateIsUnchanged:
    """`--check` still ignores unreconciled files. Stated as a test because
    the tempting next step — failing on the new bucket — would turn 30 files
    red on doppler, and that is a decision to take deliberately."""

    def test_the_check_summary_says_apply_fixes_them(
        self, project: Path
    ) -> None:
        """`--check` prints a summary, not the sections — and it carried the
        stronger version of the same untruth, since "that part is yours" was
        the ONLY thing it said about a fragment the next apply rewrites."""
        _add_header_doc(project)
        out = _cli("status", "--check", cwd=project).stdout
        assert "1 of them `jm apply` rewrites in place" in out, out

    def test_the_summary_is_unchanged_when_nothing_is_refreshable(
        self, project: Path
    ) -> None:
        _add_header_doc(project)
        assert _cli("apply", cwd=project).returncode == 0
        _hand_edit_a_body(project)
        out = _cli("status", "--check", cwd=project).stdout
        assert "rewrites in place" not in out, out
        assert "that part is yours" in out, out

    def test_an_unreconciled_only_project_still_exits_zero(
        self, project: Path
    ) -> None:
        """The gate itself. The fragment alone, every other file current."""
        _add_header_doc(project)
        assert _cli("apply", cwd=project).returncode == 0
        _hand_edit_a_body(project)
        out = _cli("status", "--check", cwd=project)
        assert out.returncode == 0, out.stdout


class TestTheReportDegradesRatherThanFails:
    """The comment beside the `except` claims status survives a pass that
    cannot be produced. A decision with no test is a comment."""

    def test_status_still_reports_when_the_pass_raises(
        self, project: Path, monkeypatch
    ) -> None:
        import contextlib

        from just_makeit import _docsync, _status

        _add_header_doc(project)

        # Only the DRY RUN — `apply` calls the same function for real during
        # status's scratch replay, and breaking that would be testing a
        # different (unguarded, pre-existing) call site.
        _real = _docsync.refresh_module_fragment_docs

        def _boom(*a, dry_run=False, **k):
            if dry_run:
                raise RuntimeError("no")
            return _real(*a, dry_run=dry_run, **k)

        monkeypatch.setattr(_docsync, "refresh_module_fragment_docs", _boom)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.suppress(SystemExit):
                _status.run(project)
        text = out.getvalue()
        assert "UNRECONCILED" in text, text
        # Falls back to where these were before: named, in the other bucket.
        assert "AUTHOR-OWNED" in text, text
        assert "native/src/m/m_ext_o.c" in text, text


class TestTheDiffIsShown:
    """`--diff` prints the difference for the new bucket too. Without it the
    section names a file and says to run apply, with nothing to look at."""

    def test_the_refreshable_diff_is_printed(self, project: Path) -> None:
        _add_header_doc(project)
        out = _cli("status", "--diff", cwd=project).stdout
        section = out[out.index("APPLY FIXES THESE") :]
        head = section.split("AUTHOR-OWNED")[0]
        assert "HEADERDOC marker." in head, head


class TestTheDryRunIsTheSameCode:
    """The report must not be a second opinion about what `apply` does."""

    @staticmethod
    def _refreshable(root: Path):
        sys.path.insert(0, str(SRC))
        import contextlib
        import io

        from just_makeit import _config as C
        from just_makeit import _docsync

        cfg = C.load(root)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return _docsync.refresh_module_fragment_docs(
                root, cfg, dry_run=True
            )

    def test_a_dry_run_writes_nothing(self, project: Path) -> None:
        _add_header_doc(project)
        before = _frag(project).read_bytes()
        got = self._refreshable(project)
        assert [p.name for p in got] == ["m_ext_o.c"]
        assert _frag(project).read_bytes() == before, "dry run wrote"

    def test_it_names_exactly_what_apply_then_changes(
        self, project: Path
    ) -> None:
        _add_header_doc(project)
        predicted = {p.name for p in self._refreshable(project)}
        before = _frag(project).read_bytes()
        assert _cli("apply", cwd=project).returncode == 0
        changed = (
            {_frag(project).name}
            if _frag(project).read_bytes() != before
            else set()
        )
        assert predicted == changed, (predicted, changed)

    def test_a_clean_project_predicts_nothing(self, project: Path) -> None:
        assert self._refreshable(project) == []
