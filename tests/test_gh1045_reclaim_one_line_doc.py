"""gh-1045: a doc that is only jm's synopsis is jm's to rebuild.

gh-1039 gave a ``single = true`` method its full runtime docstring. **No
existing module object could receive it.** `jm apply` left the old canned
literal in place, so the fix was shipped and unreachable for every project
that already had the fragment -- which is every project that had the feature.
doppler measured it: after a full apply on the fix branch,
``wfm_ext_frame.c`` still read ``"check(rx_bits) -> FrameCheck record (...)"``
verbatim, and only the one object whose fragment had been deleted by hand
moved.

`_docsync._is_jm_shaped` decides whether a slot is jm's to rebuild or a
human's to preserve, and required **two** lines to agree -- the synopsis and
the summary. gh-703 added the second because a downstream that hand-writes
richer prose keeps jm's synopsis above it, and matching on the synopsis alone
would clobber that. The canned single-record literal has no summary line at
all: it is one line, and that is the whole doc. So it matched neither
``der_head`` nor ``fb_head``, was classified hand-written, and froze.

Two independent reasons it could not reclaim, either sufficient -- the old
synopsis ends in ``.`` and the new one does not, and a one-element head can
never equal a two-element one.

**This is gh-871's lesson in mirror.** There, a "single logical line" bound on
the glue path was drawn as a cheap safety margin and instead froze the feature
shut for every gh-647 docstring. Here a two-line floor did the same to
authored members. A bound on how much text a slot holds is not a proxy for
whether a person wrote it -- which is why the fix is stated as "there is no
human prose to protect when there is no prose", and why the preservation tests
below matter more than the reclaim ones.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docsync import (  # noqa: E402
    _is_jm_shaped,
    _same_synopsis,
)
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_FRAGMENT = "native/src/wfm/wfm_ext_frame.c"
#: The literal every pre-gh-1039 project holds for a single-record method.
_CANNED = '"check(x) -> FrameCheck record (passed, stages)."'


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _set_doc(root: Path, literal: str) -> None:
    """Replace `check`'s PyMethodDef doc slot with *literal*."""
    p = root / _FRAGMENT
    t = p.read_text(encoding="utf-8")
    i = t.index('{"check",')
    j = t.index("},", i)
    p.write_text(
        t[:i]
        + '{"check", (PyCFunction)Frame_check, METH_VARARGS,\n     '
        + literal
        + t[j:],
        encoding="utf-8",
    )


def _doc(root: Path) -> str:
    """`check`'s doc slot as it stands on disk."""
    t = (root / _FRAGMENT).read_text(encoding="utf-8")
    i = t.index('{"check",')
    return t[i : t.index("},", i)]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A module object with a `single = true` method -- doppler's shape.

    A module object, deliberately: its binding lives in a **sacred**
    per-object fragment that `apply` never re-renders, which is the whole
    reason `_docsync` exists. A standalone object's `_ext.c` is regenerated
    wholesale and would have hidden this bug.
    """
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "wfm")
    _quiet(
        object_run,
        root,
        "frame",
        "wfm",
        state_vars=[("n", "int", "0")],
        arg_type="void",
        return_type="int",
    )
    _quiet(
        method_run,
        root,
        "frame",
        "check",
        "wfm",
        "uint8_t[]",
        "frame_chk_t",
        False,
        [],
        single=True,
        record_name="FrameCheck",
        result_fields=[
            {"name": "passed", "type": "int"},
            {"name": "stages", "type": "size_t"},
        ],
    )
    return root


class TestReclaim:
    """What gh-1039 could not reach."""

    def test_the_canned_one_liner_is_rebuilt(self, project):
        _set_doc(project, _CANNED)
        _quiet(apply_run, project)
        doc = _doc(project)
        assert "Parameters" in doc, doc
        assert "Returns" in doc

    def test_the_slot_was_really_frozen_first(self, project):
        """The fixture reproduces the reported state, not something else."""
        _set_doc(project, _CANNED)
        assert "Parameters" not in _doc(project)

    def test_a_trailing_period_alone_does_not_block_it(self):
        """One of the two independent reasons, in isolation."""
        der = '"run(x) -> float\\n" "\\n" "Filter it.\\n"'
        assert _is_jm_shaped('"run(x) -> float."', der, None)
        assert _same_synopsis("run(x) -> float.", "run(x) -> float")

    def test_a_different_synopsis_is_still_someone_elses(self):
        """The anchor still has to be this member's."""
        der = '"run(x) -> float\\n" "\\n" "Filter it.\\n"'
        assert not _is_jm_shaped('"run(x, gain) -> float."', der, None)
        assert not _same_synopsis("run(x) -> float", "run(x, gain) -> float")


class TestPreservation:
    """The half that makes widening safe -- and the reason to be careful.

    `_docsync` exists to not clobber hand-written bindings. Each of these
    would have passed before the change too; they are here because the change
    is what could break them.
    """

    def test_hand_written_prose_under_jms_synopsis_survives(self, project):
        """gh-703's RateConverter case, which added the two-line rule.

        A downstream keeps jm's synopsis and writes its own summary beneath.
        That is two lines, so it never reaches the one-line branch -- but it
        is the case the rule was built for and the one worth pinning.
        """
        hand = (
            '"check(x) -> FrameCheck record (passed, stages)\\n"\n'
            '     "\\n"\n'
            '     "Validates the frame against the configured layout, which\\n"\n'
            '     "is subtler than the generated summary suggests.\\n"'
        )
        _set_doc(project, hand)
        _quiet(apply_run, project)
        assert "subtler than the generated summary" in _doc(project)

    def test_a_hand_written_one_liner_survives(self, project):
        """One line, but not jm's synopsis -- so not jm's to rebuild.

        This is the case the widening could plausibly have broken: the branch
        keys on length, so the synopsis comparison above it is what keeps a
        short human sentence out of scope.
        """
        _set_doc(project, '"Validate the frame. See docs/frames.md."')
        _quiet(apply_run, project)
        assert _doc(project).count("Validate the frame.") == 1
        assert "Parameters" not in _doc(project)

    def test_an_unrelated_binding_is_untouched(self, project):
        """Preservation is structural: only named slots are transplanted."""
        p = project / _FRAGMENT
        t = p.read_text(encoding="utf-8")
        marker = "/* hand-written binding jm knows nothing about */"
        p.write_text(marker + "\n" + t, encoding="utf-8")
        _quiet(apply_run, project)
        assert marker in p.read_text(encoding="utf-8")


class TestIdempotence:
    """A transplant that runs twice must be a no-op the second time."""

    def test_a_second_apply_changes_nothing(self, project):
        _set_doc(project, _CANNED)
        _quiet(apply_run, project)
        once = (project / _FRAGMENT).read_text(encoding="utf-8")
        _quiet(apply_run, project)
        assert (project / _FRAGMENT).read_text(encoding="utf-8") == once
