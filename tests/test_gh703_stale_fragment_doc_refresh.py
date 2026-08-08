"""gh-703: `apply` must refresh a doc slot written by an *older* jm.

`_docsync._refresh_slot` decides whether a sacred fragment's doc slot is jm's
to overwrite by asking whether it is byte-for-byte the text **today's** jm
would scaffold (`ncur == nfb`, where `fb` is the fragment re-rendered with the
header ignored). That question is version-sensitive by construction: a
fragment written by an earlier release matches neither today's derived form nor
today's fallback, so it was classified hand-written and its docs froze
permanently.

The consequence is larger than the doc gap that exposed it — **every** doc
improvement jm shipped after sacred fragments existed was invisible to every
existing project, because the only way to pick one up was to delete the
fragment and lose the hand-written C it exists to protect. doppler measured it
as `grep -c Parameters` = 0 on their fragments against 22 on the matching
`.pyi`, on a jm that generates the tables correctly for a *new* fragment.

The fix matches on the **synopsis line** (`name(args) -> ret`), which jm has
always emitted first and which no realistic hand-written docstring reproduces
by accident. The two properties that matter pull against each other, so both
are pinned here:

1. a slot still carrying jm's synopsis is refreshed, whichever release wrote
   the rest of it;
2. a genuinely hand-written docstring is preserved — **for a member that can
   be hand-written**. gh-871 carved out the glue set (``destroy``,
   ``__enter__``, ``__exit__``, the serializable triplet), where the carve-out
   is not an exception to the rule but the rule applied honestly: those
   members have no declaration to attach Doxygen to, so there is no authoring
   path and the prose is jm's at any length. See `TestGlueSlotReclaim`.

Tests drive `_refresh_slot`/`transplant_docs` directly *and* a real `apply` on
a scaffolded project — the unit layer localises a break, the project layer is
the one that was actually broken.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docsync import _refresh_slot, transplant_docs  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# A doc field as it appears in a PyMethodDef entry: concatenated C literals.
DERIVED = (
    '"configure(up, dn) -> None\\n"\n'
    '     "\\n"\n'
    '     "Re-tune thresholds.\\n"\n'
    '     "\\n"\n'
    '     "Parameters\\n"\n'
    '     "----------\\n"\n'
    '     "up : float\\n"\n'
    '     "    Upper threshold.\\n"'
)
SCAFFOLD = '"configure(up, dn) -> None\\n"\n     "\\n"\n     "configure.\\n"'
# What an older jm wrote: same synopsis, a shape today's jm never produces.
OLD_JM = (
    '"configure(up, dn) -> None\\n"\n'
    '     "\\n"\n'
    '     "Re-tune thresholds.\\n"\n'
    '     "\\n"\n'
    '     "    >>> obj.configure(0.0, 0.0)\\n"'
)
HAND = (
    '"Hand-written by a human.\\n"\n'
    '     "\\n"\n'
    '     "This prose is not jm\'s.\\n"'
)


class TestRefreshSlot:
    """The decision itself, isolated from the file plumbing."""

    def test_old_jm_output_is_reclaimed(self):
        """The regression: same synopsis, unrecognised body -> still jm's."""
        assert _refresh_slot(OLD_JM, DERIVED, SCAFFOLD) == DERIVED

    def test_hand_written_is_preserved(self):
        """The property the version-sensitive test was protecting."""
        assert _refresh_slot(HAND, DERIVED, SCAFFOLD) is None

    def test_todays_scaffold_is_still_reclaimed(self):
        assert _refresh_slot(SCAFFOLD, DERIVED, SCAFFOLD) == DERIVED

    def test_already_current_is_left_alone(self):
        assert _refresh_slot(DERIVED, DERIVED, SCAFFOLD) is None

    def test_a_different_synopsis_does_not_count_as_jm_shaped(self):
        """Matching *a* synopsis is not enough — it must be jm's own."""
        other = OLD_JM.replace("configure(up, dn)", "configure(x, y, z)")
        assert _refresh_slot(other, DERIVED, SCAFFOLD) is None

    def test_prose_only_slots_keep_the_strict_rule(self):
        """reset/glue/tp_doc have no version-stable anchor to widen on.

        Their derived form carries no synopsis, so the new rule must not fire
        — otherwise any doc whose first line happened to match would be
        overwritten on the strength of one line of prose.
        """
        der = '"Reset state.\\n"\n     "\\n"\n     "Extended.\\n"'
        cur = '"Reset state.\\n"\n     "\\n"\n     "Mine.\\n"'
        assert _refresh_slot(cur, der, '"Reset state.\\n"') is None


class TestTransplantDocs:
    """The same decision through the real slot-finding machinery."""

    @staticmethod
    def _fragment(doc: str) -> str:
        return (
            "static PyMethodDef Det_methods[] = {\n"
            f'    {{"configure", (PyCFunction)Det_configure, METH_VARARGS,\n'
            f"     {doc}}},\n"
            "    {NULL}\n"
            "};\n"
        )

    def test_old_fragment_gains_the_tables(self):
        out = transplant_docs(
            self._fragment(OLD_JM),
            self._fragment(DERIVED),
            self._fragment(SCAFFOLD),
        )
        assert "Parameters" in out
        assert "Upper threshold." in out

    def test_hand_written_fragment_is_untouched(self):
        existing = self._fragment(HAND)
        out = transplant_docs(
            existing, self._fragment(DERIVED), self._fragment(SCAFFOLD)
        )
        assert out == existing


# ── real project ────────────────────────────────────────────────────────────

_AUTHORED = """/**
 * @brief Re-tune thresholds and verify counts.
 *
 * A live lock survives a retune; the counters are not reset.
 *
 * @param state The detector.
 * @param up Upper threshold, in dB.
 * @param dn Lower threshold, in dB.
 */"""

_COMPACT = """{"configure", (PyCFunction)(void *)Det_configure, \
METH_VARARGS | METH_KEYWORDS,
     "configure(up, dn) -> None\\n"
     "\\n"
     "Re-tune thresholds and verify counts.\\n"
     "\\n"
     "    >>> obj.configure(0.0, 0.0)\\n"},"""

_HAND_C = """{"configure", (PyCFunction)(void *)Det_configure, \
METH_VARARGS | METH_KEYWORDS,
     "Hand-written by a human. Do not touch.\\n"},"""


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    """A module object with an authored header; returns (root, fragment)."""
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "detection")
    object_run(
        root,
        "det",
        "detection",
        state_vars=[("thresh", "double", "1.0")],
        arg_type="double[]",
        return_type="double",
    )
    method_run(
        root,
        "det",
        "configure",
        "detection",
        "void",
        "void",
        False,
        [],
        params=[("up", "double"), ("dn", "double")],
    )
    hdr = root / "native" / "inc" / "det" / "det_core.h"
    text = hdr.read_text(encoding="utf-8")
    old = re.search(
        r"/\*\*\n(?: \*[^\n]*\n)+ \*/(?=\nvoid det_configure)", text
    )
    assert old, "the scaffold no longer emits a block above det_configure"
    hdr.write_text(
        text[: old.start()] + _AUTHORED + text[old.end() :], encoding="utf-8"
    )
    apply_run(root)
    frag = next((root / "native/src/detection").glob("*_ext_det.c"))
    return root, frag


def _replace_entry(frag: Path, new: str) -> None:
    t = frag.read_text(encoding="utf-8")
    start = t.index('{"configure"')
    end = t.index("},", t.index("configure(", start)) + 2
    frag.write_text(t[:start] + new + t[end:], encoding="utf-8")


class TestRealApply:
    """What actually broke: a fragment on disk, through `jm apply`."""

    def test_authored_header_reaches_a_fresh_fragment(self, project):
        """Baseline — the generator side was never the bug."""
        _, frag = project
        assert "Upper threshold, in dB." in frag.read_text()

    def test_a_stale_fragment_is_refreshed(self, project):
        """The reported symptom: tables absent, and apply never restored
        them."""
        root, frag = project
        _replace_entry(frag, _COMPACT)
        assert "Parameters" not in _configure_entry(frag)
        apply_run(root)
        entry = _configure_entry(frag)
        assert "Parameters" in entry
        assert "Upper threshold, in dB." in entry
        assert "A live lock survives a retune" in entry

    def test_a_hand_written_fragment_survives_apply(self, project):
        root, frag = project
        _replace_entry(frag, _HAND_C)
        apply_run(root)
        assert "Hand-written by a human." in _configure_entry(frag)

    def test_refresh_is_idempotent(self, project):
        root, frag = project
        _replace_entry(frag, _COMPACT)
        apply_run(root)
        once = frag.read_text(encoding="utf-8")
        apply_run(root)
        assert frag.read_text(encoding="utf-8") == once


def _configure_entry(frag: Path) -> str:
    t = frag.read_text(encoding="utf-8")
    start = t.index('{"configure"')
    return t[start : t.index("},", start) + 2]


# ── glue slots (gh-707) ─────────────────────────────────────────────────────
#
# For a glue slot the derived and fallback renders are IDENTICAL: `_gluedoc`
# never consults `doc_blocks`, so rendering the fragment with the header and
# without it produces the same text. That defeats every branch above — not
# "already current", not equal to the scaffold form, no synopsis for
# `_is_jm_shaped`, and the empty-slot rule requires `nder != nfb`, which is a
# test for *header*-derived content that jm-authored prose can never pass.
#
# So a glue slot could never be refreshed at all. doppler measured 394 of them
# still carrying pre-gh-647 one-liners after adopting gh-703, which was 57% of
# their entire remaining runtime-incomplete count.

GLUE_DER = (
    '"Size in bytes of this object\'s serialized state.\\n"\n'
    '     "\\n"\n'
    '     "The exact length get_state returns.\\n"'
)
GLUE_LEGACY = '"Serialized state size in bytes.\\n"'
GLUE_RICH_HAND = (
    '"Mine, by hand.\\n"\n'
    '     "\\n"\n'
    '     "With a second paragraph, so not a one-liner.\\n"'
)


class TestGlueSlotReclaim:
    """jm owns glue prose outright — there is no header to author against."""

    def test_legacy_one_liner_is_reclaimed(self):
        """The 394-slot case. Note der == fb, as it always is for glue."""
        assert (
            _refresh_slot(GLUE_LEGACY, GLUE_DER, GLUE_DER, "state_bytes")
            == GLUE_DER
        )

    def test_empty_slot_is_filled(self):
        """`__enter__` was `NULL` — the same defect, not a separate one.

        The pre-existing empty-slot rule cannot fire here because it requires
        `nder != nfb` to prove the content is "real Doxygen", and glue content
        is jm-authored rather than header-derived.
        """
        assert _refresh_slot("NULL", GLUE_DER, GLUE_DER, "__enter__")

    def test_a_rich_glue_doc_is_reclaimed_too(self):
        """gh-871 inverted this: the one-line bound froze the feature shut.

        It used to assert the opposite — that a multi-line glue docstring was
        preserved — on the reasoning that length is evidence somebody wrote
        it by hand. It is not. There is **no authoring path** for a glue
        member (no declaration to hang Doxygen on), so every one of these
        docstrings is jm's, at any length.

        What the bound actually did was freeze glue prose permanently: every
        gh-647 glue docstring is multi-paragraph, so once a project had one,
        no later jm could revise it. gh-805 §H, gh-864 and gh-869 each fixed
        this prose and each reached only a *fresh* fragment — three releases
        landing correctly everywhere except the projects that already existed.

        The trade is that a hand-edited glue docstring is now overwritten.
        It is overwritten **by name**: `refresh_module_fragment_docs` reports
        every reclaimed member, so it shows up in the apply output and in the
        diff rather than silently.
        """
        assert (
            _refresh_slot(GLUE_RICH_HAND, GLUE_DER, GLUE_DER, "get_state")
            == GLUE_DER
        )

    def test_a_glue_doc_already_current_is_left_alone(self):
        # The reclaim being unconditional must not mean "always edit" — an
        # identical slot still returns None, or every apply would rewrite
        # every fragment and report a reclaim that changed nothing.
        assert _refresh_slot(GLUE_DER, GLUE_DER, GLUE_DER, "get_state") is None

    def test_a_non_glue_name_is_unaffected(self):
        """Scoped to the closed glue set — no other slot kind changes."""
        assert (
            _refresh_slot(GLUE_LEGACY, GLUE_DER, GLUE_DER, "execute") is None
        )

    def test_both_close_spellings_are_glue(self):
        from just_makeit._gluedoc import glue_method_names

        names = glue_method_names()
        assert {"destroy", "close"} <= names, (
            "an object is either destroy- or close-shaped and the transplant "
            "sees only the name, so both spellings must be recognised"
        )
        assert {"state_bytes", "get_state", "set_state"} <= names
        assert {"__enter__", "__exit__"} <= names
