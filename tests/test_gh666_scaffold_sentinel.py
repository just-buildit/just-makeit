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

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    extract_doc_blocks,
    is_scaffold_doc,
    parse_doxygen_block,
    scaffold_briefs,
    scaffold_doc_block,
)
from just_makeit._method import run as method_run  # noqa: E402
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


class TestSkeletonEmitter:
    """What jm writes must be what jm refuses to derive."""

    def test_skeleton_round_trips_to_undocumented(self):
        decl = "size_t fir_run(fir_state_t *s, const float *x, size_t n);"
        doc = scaffold_doc_block(decl, "run")
        assert parse_doxygen_block(doc, name="run") is None

    def test_skeleton_lists_every_parameter_in_order(self):
        decl = "void fir_tune(fir_state_t *state, double x, double hz);"
        doc = scaffold_doc_block(decl, "tune")
        assert [ln for ln in doc.splitlines() if "@param" in ln] == [
            " * @param state",
            " * @param x",
            " * @param hz",
        ]

    def test_no_invented_descriptions(self):
        # An invented description is not documentation, and once written jm
        # can no longer tell it from prose a human typed.
        decl = "void fir_tune(fir_state_t *state, double hz);"
        doc = scaffold_doc_block(decl, "tune")
        assert "parameter." not in doc
        assert "Must be non-NULL" not in doc

    def test_return_only_when_a_value_is_returned(self):
        assert "@return" in scaffold_doc_block("int f_go(void *s);", "go")
        assert "@return" not in scaffold_doc_block("void f_go(void *s);", "go")
        # A pointer return is still a value.
        assert "@return" in scaffold_doc_block("char *f_go(void *s);", "go")

    def test_no_code_block(self):
        # A placeholder example would be executed by the generated project's
        # doctest gate.
        assert "@code" not in scaffold_doc_block("int f_go(void *s);", "go")

    def test_undescribable_decl_gets_no_skeleton(self):
        # Better no comment than one that names a parameter the signature does
        # not have -- that is what Doxygen's paramdoc warning reports.
        assert scaffold_doc_block("void f(void (*cb)(int));", "f") == ""
        assert scaffold_doc_block("#define FOO 1", "foo") == ""

    def test_void_param_list_yields_no_params(self):
        doc = scaffold_doc_block("int f_go(void);", "go")
        assert "@param" not in doc


class TestNearestBlockWins:
    """gh-666: a hand-written block above the skeleton must not be ignored."""

    def test_second_of_two_adjacent_blocks_binds(self):
        # The decl group could previously begin with the newline after `*/`
        # and swallow the whole second comment (no `;{}` inside it to stop
        # the run), binding the FIRST block -- so an author who wrote a new
        # block above jm's skeleton silently lost to the skeleton.
        text = (
            "/**\n * @brief scale.\n * @param x\n */\n"
            "/**\n * @brief Scale a sample.\n */\n"
            "float gain_scale(gain_state_t *state, float x);\n"
        )
        assert "Scale a sample." in extract_doc_blocks(text)["gain_scale"]

    def test_pointer_parameters_still_match(self):
        # `*` must stay legal in a decl -- excluding it to stop the run from
        # crossing a comment would drop every pointer signature.
        text = "/**\n * @brief go.\n */\nint f_go(const float *x, void *s);\n"
        assert "f_go" in extract_doc_blocks(text)


class TestMethodScaffoldEndToEnd:
    """`jm method` writes the skeleton, and replay never re-stamps it."""

    @staticmethod
    def _project(tmp_path):
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
        method_run(
            root,
            "fir",
            "tune",
            None,
            "double",
            "void",
            False,
            [],
            params=[("hz", "double")],
        )
        return root, root / "native" / "inc" / "fir" / "fir_core.h"

    def test_method_gets_a_skeleton(self, tmp_path):
        _root, header = self._project(tmp_path)
        text = header.read_text(encoding="utf-8")
        assert "@brief tune." in text
        assert " * @param hz\n" in text

    def test_skeleton_is_not_derived(self, tmp_path):
        # The whole point: a scaffolded method reads as undocumented, so the
        # .pyi matches a manifest-only rebuild that never saw the header.
        root, _header = self._project(tmp_path)
        assert "fir_tune" not in _load_doc_blocks(root, "fir")

    def test_apply_does_not_restamp(self, tmp_path):
        root, header = self._project(tmp_path)
        before = header.read_text(encoding="utf-8")
        apply_run(root)
        after = header.read_text(encoding="utf-8")
        assert after == before
        assert after.count("@brief tune.") == 1

    def test_authored_prose_survives_and_derives(self, tmp_path):
        root, header = self._project(tmp_path)
        filled = (
            header.read_text(encoding="utf-8")
            .replace(" * @brief tune.", " * @brief Retune the filter.")
            .replace(
                " * @param hz", " * @param hz  New centre frequency in Hz."
            )
        )
        header.write_text(filled, encoding="utf-8")
        apply_run(root)
        assert header.read_text(encoding="utf-8") == filled
        blk = _load_doc_blocks(root, "fir")["fir_tune"]
        assert blk.brief == "Retune the filter."
        assert blk.param_desc("hz") == "New centre frequency in Hz."


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
