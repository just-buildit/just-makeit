"""gh-717: 79-col wrapping in a C header must not fake or break a list.

gh-653 taught jm to preserve an authored list instead of folding it into one
run-on line. Two shapes then broke, and both are *created by the 79-column
wrapping a C header requires* — so they recur for any header long enough to
need it, which is all of them.

1. **A wrapped item tears.** A continuation line carries no marker, so it
   read as a new paragraph and the bullet came apart into a bullet plus an
   orphan sentence.
2. **A wrapped sentence fakes a numbered item.** ``0)`` closing a
   parenthetical lands at line-start, matches the numbered-list detector, and
   splits the sentence in half.

Fixing (1) then collides with gh-744: folding the continuation back makes the
item longer than the 79 columns everything else is held to — doppler's
``nearest:`` bullet comes back at 118. So the item is re-wrapped *within
itself*, with a hanging indent, and only when it actually overflows. That last
clause is what keeps gh-653's promise for the items that already fit: their
intra-column alignment is the author's, and `_wrap` splits on whitespace, so
re-flowing one that did not need it would destroy it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    DOC_WIDTH,
    group_paragraphs,
    parse_doxygen_block,
    render_numpy_doc,
    starts_list_item,
    wrap_structured_line,
)

TARGET = 79


def _block(raw: str):
    return parse_doxygen_block(raw)


class TestWrappedContinuationFolds:
    """Cause 1 — a more-indented, marker-less line belongs to its item."""

    RAW = """/**
 * @brief Select a mode.
 *
 *   - nearest: the floor or the next index, whichever `point` is
 *              closer to (an exact 0.5 tie selects the floor index)
 *   - linear:  linear interpolation between the floor index and the
 *              next one, at the fractional position between them
 */
void demo_set(demo_state_t *s, int mode);"""

    def test_the_list_has_exactly_two_items(self):
        paras = group_paragraphs(_block(self.RAW).body)
        (listing,) = [p for p in paras if p.startswith("- ")]
        assert len(listing.split("\n")) == 2, listing

    def test_no_orphan_paragraph_survives(self):
        """The bug's signature: continuation text as its own paragraph."""
        paras = group_paragraphs(_block(self.RAW).body)
        assert not [
            p
            for p in paras
            if p.startswith("closer to") or p.startswith("next one")
        ], paras

    def test_the_continuation_text_is_not_lost(self):
        """Guard the guard — dropping it would also pass the test above."""
        flat = " ".join(
            " ".join(group_paragraphs(_block(self.RAW).body)).split()
        )
        assert "closer to (an exact 0.5 tie selects the floor index)" in flat
        assert "at the fractional position between them" in flat

    def test_an_unindented_continuation_is_still_prose(self):
        """The rule is *more indented*, not merely *after*.

        A marker-less line at the item's own indent is a new paragraph, and
        was before this change too — folding it would be a guess.
        """
        paras = group_paragraphs(["- item one", "a new paragraph"])
        assert paras == ["- item one", "a new paragraph"]


class TestNumberedMarkerNeedsAPlaceToStart:
    """Cause 2 — `N)` mid-sentence is a wrap artifact, not a list."""

    RAW = """/**
 * @brief Reset.
 *
 * Zeroes both sample clocks (so `elapsed_s` and the carrier phase restart at
 * 0) and clears the resampler's delay line and fractional accumulator.
 */
void demo_reset(demo_state_t *s);"""

    def test_the_sentence_stays_whole(self):
        paras = group_paragraphs(_block(self.RAW).body)
        assert len(paras) == 1, paras
        assert "restart at 0) and clears" in paras[0]

    def test_a_real_numbered_list_after_a_lead_in_still_renders(self):
        """`Modes:` then `1.` is the common authored shape.

        Requiring a blank line before every numbered list — the issue's
        first suggestion — would have regressed this.
        """
        paras = group_paragraphs(["Modes:", "1. fast", "2. slow"])
        assert paras == ["Modes:", "1. fast\n2. slow"]

    def test_a_real_numbered_list_after_a_blank_line_still_renders(self):
        paras = group_paragraphs(["Intro text.", "", "1. fast", "2. slow"])
        assert paras == ["Intro text.", "1. fast\n2. slow"]

    def test_a_numbered_list_at_the_very_start_still_renders(self):
        assert group_paragraphs(["1. fast", "2. slow"]) == ["1. fast\n2. slow"]

    def test_the_predicate_directly(self):
        assert starts_list_item("1. fast", [], False) is True
        assert starts_list_item("1. fast", ["Modes:"], False) is True
        assert starts_list_item("2. slow", ["x"], True) is True
        assert (
            starts_list_item("0) and clears it.", ["restart at"], False)
            is False
        )

    def test_bullets_are_unaffected_by_the_new_rule(self):
        """Only numbered markers are ambiguous; a `-` never occurs mid-word."""
        assert starts_list_item("- fast", ["Modes are"], False) is True
        assert group_paragraphs(["Modes", "- fast"]) == ["Modes", "- fast"]


class TestItemsWrapWithinThemselves:
    """The gh-744 collision: a folded item must still fit 79 columns."""

    def test_a_long_item_is_wrapped_with_a_hanging_indent(self):
        item = "- nearest: " + "word " * 30
        out = wrap_structured_line(item.strip(), DOC_WIDTH)
        assert len(out) > 1
        assert max(len(ln) for ln in out) <= DOC_WIDTH
        assert out[0].startswith("- nearest:")
        assert all(ln.startswith("  ") for ln in out[1:]), out

    def test_an_item_that_fits_is_returned_byte_identical(self):
        """gh-653's promise, kept for every list short enough to keep it."""
        item = "- floor:   nearest index at or below the point"
        assert wrap_structured_line(item, DOC_WIDTH) == [item]

    def test_a_table_row_is_never_wrapped(self):
        """Its columns are its meaning; a wrapped table is a broken one."""
        row = "| " + " | ".join(["column"] * 20) + " |"
        assert len(row) > DOC_WIDTH  # precondition
        assert wrap_structured_line(row, DOC_WIDTH) == [row]

    def test_the_rendered_docstring_fits_the_target(self):
        raw = """/**
 * @brief Select a mode.
 *
 *   - nearest: the floor or the next index, whichever `point` is
 *              closer to (an exact 0.5 tie selects the floor index)
 *   - linear:  linear interpolation between the floor index and the
 *              next one, at the fractional position between them
 */
void demo_set(demo_state_t *s, int mode);"""
        lines = render_numpy_doc(_block(raw), "set", [], "None", indent=8)
        assert max(len(ln) for ln in lines) <= TARGET, [
            ln for ln in lines if len(ln) > TARGET
        ]
        # and the list is still a list
        assert sum(1 for ln in lines if ln.strip().startswith("- ")) == 2
