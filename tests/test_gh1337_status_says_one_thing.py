"""`jm status` must not contradict itself about a sacred file (gh-1337).

Reported from doppler against 0.76.2, in a single screen::

    STALE (2) — `jm apply` will rewrite from the manifest:
      ~ native/src/acq/acq_core.c
      Run `jm apply` to sync (glue regenerated; your _core.c is kept).

    Your `_core.c` is sacred — apply never changes it; use ...

Three claims, and no two of them agree. The header says apply will rewrite
the file; the line under the list says it is kept; the footer says apply
never changes it at all.

**Both absolutes were false, in opposite directions.** gh-1294 taught apply
to splice a declared method's missing body into `_core.c` -- a change, to a
sacred file, by design. So "never changes it" is wrong. And it never edits or
removes a line the author wrote, so "will rewrite from the manifest" is wrong
too. Stating either one made the other look like a defect: an appended body
read as a bug in the reader's own tree, and when it WAS a bug (gh-1328) the
footer argued it had not happened.

The fix is to say the true thing, which needs the list split by WHO OWNS the
file -- one header cannot describe both, because apply does two different
things. Ownership is `_createonly`'s question and it already answers it; this
module must not grow a second way to ask.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _project_with_a_missing_body(tmp_path) -> Path:
    """A sacred `_core.c` the manifest declares more of than it defines.

    The body is removed by hand rather than by running jm, so the drift is
    real regardless of what the renderer does.
    """
    root = tmp_path / "p"
    _silent(new_run, "p", root)
    _silent(object_run, root, "acq", None, state_vars=[("n", "size_t", "4")])
    c = root / "native/src/acq/acq_core.c"
    t = c.read_text(encoding="utf-8")
    i = t.index("acq_reset(")
    start = t.rindex("\n", 0, t.rindex("\n", 0, i)) + 1
    end = t.index("\n}\n", i) + 3
    c.write_text(t[:start] + t[end:], encoding="utf-8")
    return root


def _status(root: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            status_run(root)
        except SystemExit:
            pass
    return buf.getvalue()


class TestItDoesNotContradictItself:
    def test_the_sacred_file_is_listed(self, tmp_path):
        """Guard the guard: if the file stops being reported at all, every
        assertion below passes by vacuum."""
        out = _status(_project_with_a_missing_body(tmp_path))
        assert "acq_core.c" in out, out

    def test_it_never_claims_apply_rewrites_the_authors_file(self, tmp_path):
        out = _status(_project_with_a_missing_body(tmp_path))
        head = out[: out.index("acq_core.c")]
        assert "will rewrite" not in head, (
            "the header above a sacred file says apply will rewrite it:\n"
            f"{out}"
        )

    def test_it_never_claims_apply_changes_nothing(self, tmp_path):
        """The opposite absolute, which is the one that hid gh-1328."""
        out = _status(_project_with_a_missing_body(tmp_path))
        assert "never changes it" not in out, out

    def test_it_says_what_apply_actually_does(self, tmp_path):
        out = _status(_project_with_a_missing_body(tmp_path))
        assert "ADD" in out and "never rewriting" in out, out


class TestTheClaimIsTrue:
    """Wording is only worth gating if it matches behaviour."""

    def test_apply_adds_the_missing_definition_and_nothing_else(
        self, tmp_path
    ):
        root = _project_with_a_missing_body(tmp_path)
        c = root / "native/src/acq/acq_core.c"
        before = c.read_text(encoding="utf-8")
        _silent(apply_run, root)
        after = c.read_text(encoding="utf-8")
        assert after != before, "apply added nothing; the message is a lie"
        assert before.rstrip() in after or after.startswith(
            before[: before.index("\n}\n")]
        ), "apply rewrote existing content rather than appending"
        assert "acq_reset(" in after, after

    def test_the_authors_own_lines_survive(self, tmp_path):
        """The half the footer promises: nothing the author wrote is edited
        or removed."""
        root = _project_with_a_missing_body(tmp_path)
        c = root / "native/src/acq/acq_core.c"
        marked = c.read_text(encoding="utf-8").replace(
            "#include", "/* AUTHOR MARK */\n#include", 1
        )
        c.write_text(marked, encoding="utf-8")
        _silent(apply_run, root)
        assert "/* AUTHOR MARK */" in c.read_text(encoding="utf-8")


class TestGlueKeepsItsOwnWording:
    """Splitting the list must not soften the message for jm's own files --
    apply really does rewrite those, and the reader needs to know."""

    def test_a_stale_glue_file_still_says_rewrite(self, tmp_path):
        root = tmp_path / "p"
        _silent(new_run, "p", root)
        _silent(
            object_run, root, "acq", None, state_vars=[("n", "size_t", "4")]
        )
        ext = root / "native/src/acq/acq_ext.c"
        ext.write_text(
            ext.read_text(encoding="utf-8") + "\n/* drift */\n",
            encoding="utf-8",
        )
        out = _status(root)
        assert "acq_ext.c" in out, out
        assert "will rewrite" in out, out
