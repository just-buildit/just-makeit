"""gh-666 Phase 0: one definition of what jm's own scaffold Doxygen looks like.

Two peer implementations of this predicate existed and disagreed. The parser's
copy (inside ``parse_doxygen_block``) recognised ``@brief <member>.`` only for a
block carrying nothing else at all; ``_object._is_scaffold_brief`` carried its
own template set and ignored the rest of the block entirely. Nothing caught the
divergence because the two were reached by different callers.

It matters now because gh-666 has jm scaffold a doc skeleton above every new
declaration. A skeleton is jm's own output, so it must read as *undocumented*
until an author fills it — otherwise the generated ``.pyi`` presents jm's
boilerplate as if a human wrote it, and a manifest-only rebuild (no header to
read) stops agreeing with a fresh scaffold, which is the idempotence contract.

The sentinel has two strengths, and the difference is the point:

* one of jm's **specific** templates (``Get current gain.``) is conclusive on
  its own — jm wrote that string;
* the generic ``@brief <member>.`` is equally what a terse author writes, so it
  only counts when nothing else in the block was filled in.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    is_scaffold_doc,
    parse_doxygen_block,
    scaffold_briefs,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import _is_scaffold_brief, _load_doc_blocks  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _blk(text: str, name: str | None = None):
    return parse_doxygen_block(text, name=name)


class TestGenericSentinel:
    """``@brief <member>.`` — weak evidence, needs corroboration."""

    def test_bare_name_brief_alone_is_scaffold(self):
        assert is_scaffold_doc(_blk("@brief tune."), "tune")

    def test_underscores_and_case_fold(self):
        assert is_scaffold_doc(_blk("@brief Set Tap Count."), "set_tap_count")

    def test_a_written_param_keeps_the_block(self):
        # The half-filled skeleton: author documented a param, left the brief.
        # Dropping the block here would discard prose a human wrote.
        blk = _blk("@brief tune.\n@param hz  Tuning frequency in Hz.")
        assert not is_scaffold_doc(blk, "tune")

    def test_a_written_return_keeps_the_block(self):
        blk = _blk("@brief tune.\n@return Previous frequency.")
        assert not is_scaffold_doc(blk, "tune")

    def test_body_prose_keeps_the_block(self):
        blk = _blk("@brief tune.\n\nRetunes the filter in place.")
        assert not is_scaffold_doc(blk, "tune")

    def test_an_example_keeps_the_block(self):
        # No jm scaffold emits @code -- a runnable placeholder would be
        # executed by the generated project's doctest gate -- so its presence
        # proves an author has been here.
        blk = _blk("@brief tune.\n@code\n>>> obj.tune(1.0)\n@endcode")
        assert not is_scaffold_doc(blk, "tune")

    def test_a_real_brief_is_never_scaffold(self):
        assert not is_scaffold_doc(_blk("@brief Retune the filter."), "tune")

    def test_no_member_name_means_no_match(self):
        assert not is_scaffold_doc(_blk("@brief tune."), "")


class TestTemplateSentinel:
    """jm's specific template strings — conclusive on their own."""

    def test_accessor_template_survives_generated_params(self):
        # jm's accessor scaffold DOES emit `@param state  Must be non-NULL.`,
        # and those blocks reach real headers today. If generated params
        # rescued a template brief, every get_/set_ would start deriving jm's
        # own boilerplate into the .pyi.
        blk = _blk("@brief Get current g.\n@param state  Must be non-NULL.")
        assert is_scaffold_doc(blk, "get_g")

    def test_lifecycle_template_needs_the_owner(self):
        blk = _blk("@brief Create a fir instance.")
        assert is_scaffold_doc(blk, "create", "fir")
        # Without the owner the template cannot be reconstructed, so it is
        # simply not matched -- never matched against the wrong owner.
        assert not is_scaffold_doc(blk, "create", "biquad")

    def test_step_shape_templates(self):
        blk = _blk("@brief Process one input sample.")
        assert is_scaffold_doc(blk, "step")
        # The same brief on a method jm did not scaffold is real prose.
        assert not is_scaffold_doc(blk, "tune")

    def test_body_still_overrides_a_template(self):
        blk = _blk("@brief Get current g.\n\nThe gain is applied post-mix.")
        assert not is_scaffold_doc(blk, "get_g")

    def test_scaffold_briefs_excludes_the_bare_name(self):
        # The bare name is the *generic* sentinel and is handled separately;
        # folding it in here would silently upgrade it to conclusive.
        assert scaffold_briefs("tune") == set()
        # ...except where a template legitimately coincides with it, which is
        # correct: jm really does scaffold `@brief Set g.` for set_g.
        assert "set g" in scaffold_briefs("set_g")


class TestOnePredicate:
    """The parser and _object must not drift apart again."""

    def test_object_helper_delegates(self):
        blk = _blk("@brief Get current g.\n@param state  Must be non-NULL.")
        assert _is_scaffold_brief("fir", "get_g", blk) is is_scaffold_doc(
            blk, "get_g", "fir"
        )

    def test_parser_and_object_agree_on_a_skeleton(self):
        raw = "@brief tune.\n@param state\n@param hz"
        # The parser drops it outright...
        assert parse_doxygen_block(raw, name="tune") is None
        # ...and the predicate agrees, so neither path can derive it.
        assert is_scaffold_doc(parse_doxygen_block(raw), "tune")


class TestScaffoldedHeaderDerivesNothing:
    """The idempotence contract, end to end on a real scaffold."""

    def test_fresh_header_yields_no_doc_blocks(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root)
        object_run(
            root,
            "fir",
            None,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        header = root / "native" / "inc" / "fir" / "fir_core.h"
        # The scaffold really does write Doxygen (lifecycle + accessors)...
        assert "@brief" in header.read_text(encoding="utf-8")
        # ...and none of it is derived, so a manifest-only rebuild -- which has
        # no header at all -- produces the same .pyi.
        assert _load_doc_blocks(root, "fir") == {}
