"""Unit tests for just_makeit._docstring (Doxygen → numpy derivation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    DoxyBlock,
    _strip_doxy_inline,
    extract_doc_blocks,
    parse_doxygen_block,
    render_numpy_doc,
    render_numpy_method_doc,
)


class TestStripDoxyInline:
    def test_param_reference(self):
        assert (
            _strip_doxy_inline("length @p code_len; must equal @p sf")
            == "length code_len; must equal sf"
        )

    def test_code_and_ref(self):
        assert (
            _strip_doxy_inline("see @c foo and @ref bar") == "see foo and bar"
        )

    def test_leaves_other_tags(self):
        # @brief/@param are line tags, not inline; @pre is not a single-word ref
        assert _strip_doxy_inline("@pre x > 0") == "@pre x > 0"

    def test_non_word_arguments(self):
        # gh-641: Doxygen marks the next *token*, which need not be an
        # identifier. These are all idiomatic and all used to survive raw.
        assert (
            _strip_doxy_inline("clamped to @c +/-10^(clip_db/20)")
            == "clamped to +/-10^(clip_db/20)"
        )
        assert (
            _strip_doxy_inline('BLUE type @c "A" is ASCII')
            == 'BLUE type "A" is ASCII'
        )
        assert _strip_doxy_inline("returns @c -1 on error") == (
            "returns -1 on error"
        )

    def test_backslash_prefix(self):
        # gh-650: \p and @p are the same Doxygen command.
        assert _strip_doxy_inline(r"length \p code_len") == "length code_len"

    def test_does_not_eat_longer_identifiers(self):
        # `@pattern` is not `@p` + `attern` — a marker needs whitespace after.
        assert _strip_doxy_inline("@pattern matched") == "@pattern matched"

    def test_parse_strips_param_and_body(self):
        b = parse_doxygen_block(
            "/**\n"
            " * @brief Do a thing with @p n samples.\n"
            " * @param n Count of @p x items.\n"
            " */"
        )
        assert "@p" not in b.brief
        assert "@p" not in b.param_desc("n")
        assert b.param_desc("n") == "Count of x items."


# Real-shaped block from doppler's ddc_core.h (ddc_execute).
_EXECUTE_BLOCK = """\
/**
 * @brief Mix and resample a block of CF32 samples.
 *
 * @param state    Must be non-NULL.
 * @param x        Input samples, complex64, length x_len.
 * @param x_len    Number of input samples.
 * @param out      Output buffer, complex64, capacity max_out.
 * @param max_out  Maximum output samples to write.
 * @return Number of output samples written.
 */"""


class TestParseDoxygenBlock:
    def test_full_block(self):
        b = parse_doxygen_block(_EXECUTE_BLOCK)
        assert b is not None
        assert b.brief == "Mix and resample a block of CF32 samples."
        assert b.returns == "Number of output samples written."
        names = [n for n, _ in b.params]
        assert names == ["state", "x", "x_len", "out", "max_out"]
        assert b.param_desc("x") == "Input samples, complex64, length x_len."

    def test_single_line_block_is_brief(self):
        b = parse_doxygen_block(
            "/** Return the configured output/input rate ratio. */"
        )
        assert b is not None
        assert b.brief == "Return the configured output/input rate ratio."
        assert b.params == []
        assert b.returns == ""

    def test_body_paragraphs(self):
        raw = """\
/**
 * @brief Find peaks.
 *
 * Scans for local maxima above a threshold, then sorts by amplitude.
 *
 * @param db  dB spectrum.
 */"""
        b = parse_doxygen_block(raw)
        assert b.brief == "Find peaks."
        assert any("local maxima" in ln for ln in b.body)
        assert b.param_desc("db") == "dB spectrum."

    def test_multiline_param_description(self):
        raw = """\
/**
 * @brief Do a thing.
 * @param x  First line of the description
 *           that continues onto a second line.
 * @return Something.
 */"""
        b = parse_doxygen_block(raw)
        assert b.param_desc("x") == (
            "First line of the description that continues onto a second line."
        )
        assert b.returns == "Something."

    def test_brief_without_tag(self):
        raw = "/**\n * Just a leading sentence, no tag.\n */"
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.brief == "Just a leading sentence, no tag."

    def test_trivial_template_brief_returns_none(self):
        # jm's own scaffold writes "@brief myverb." — not real documentation.
        raw = "/**\n * @brief myverb.\n */"
        assert parse_doxygen_block(raw, name="myverb") is None
        # ...but with a real param it is kept.
        raw2 = "/**\n * @brief myverb.\n * @param x  A value.\n */"
        assert parse_doxygen_block(raw2, name="myverb") is not None

    def test_empty_block_returns_none(self):
        assert parse_doxygen_block("/** */") is None
        assert parse_doxygen_block("/**\n *\n */") is None

    def test_malformed_param_tag_does_not_crash(self):
        raw = "/**\n * @brief Hi.\n * @param\n * @return ok\n */"
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.brief == "Hi."
        assert b.returns == "ok"


class TestExtractDocBlocks:
    def test_maps_function_names_to_blocks(self):
        header = (
            _EXECUTE_BLOCK.replace("/**", "  /**")
            + "\nsize_t ddc_execute(ddc_state_t *state, const float complex "
            "*x, size_t x_len, float complex *out, size_t max_out);\n"
            "\n/** Return the rate. */\n"
            "double ddc_get_rate(const ddc_state_t *state);\n"
        )
        blocks = extract_doc_blocks(header)
        assert "ddc_execute" in blocks
        assert "ddc_get_rate" in blocks
        assert "Mix and resample" in blocks["ddc_execute"]
        b = parse_doxygen_block(blocks["ddc_execute"])
        assert b.brief == "Mix and resample a block of CF32 samples."

    def test_undocumented_decl_absent(self):
        header = "double ddc_get_rate(const ddc_state_t *state);\n"
        assert extract_doc_blocks(header) == {}

    def test_inline_definition_extracted(self):
        # gh-385: a function *defined* inline in the header (body in `{ ... }`
        # instead of a `;`-terminated prototype, e.g. a JM_FORCEINLINE
        # cic_decimate / step()) must still have its Doxygen extracted.
        header = (
            "/**\n"
            " * @brief Decimate a block of samples.\n"
            " * @param state The state.\n"
            " */\n"
            "JM_FORCEINLINE JM_HOT size_t\n"
            "cic_decimate(cic_state_t *state, const float complex *in,\n"
            "             size_t n_in, float complex *out)\n"
            "{\n"
            "    return 0;\n"
            "}\n"
        )
        blocks = extract_doc_blocks(header)
        assert "cic_decimate" in blocks
        assert (
            parse_doxygen_block(blocks["cic_decimate"]).brief
            == "Decimate a block of samples."
        )

    def test_brace_block_without_params_is_not_matched(self):
        # The `{`-terminator allowance must not pick up a braced block that has
        # no parameter list — e.g. a documented `typedef struct { ... }`.
        header = "/** A state struct. */\ntypedef struct { int n; } foo_t;\n"
        assert extract_doc_blocks(header) == {}


class TestRenderNumpyMethodDoc:
    def test_drops_c_only_params_keeps_python_args(self):
        b = parse_doxygen_block(_EXECUTE_BLOCK)
        summary, body, descs, ret, _ex = render_numpy_method_doc(
            b, py_params=[("x", "NDArray[complex64]")]
        )
        assert summary == "Mix and resample a block of CF32 samples."
        # only the Python-facing `x` survives; state/x_len/out/max_out dropped
        assert set(descs) == {"x"}
        assert descs["x"] == "Input samples, complex64, length x_len."
        assert ret == "Number of output samples written."

    def test_positional_zip_when_name_differs(self):
        raw = """\
/**
 * @brief Scale.
 * @param samples  The input array.
 * @return Scaled output.
 */"""
        b = parse_doxygen_block(raw)
        _s, _b, descs, _r, _ex = render_numpy_method_doc(
            b, py_params=[("x", "NDArray[float32]")]
        )
        # C `samples` aligns positionally to Python `x`
        assert descs["x"] == "The input array."

    def test_missing_param_desc_is_empty_string(self):
        b = DoxyBlock(brief="Hi.")
        _s, _b, descs, _r, _ex = render_numpy_method_doc(
            b, py_params=[("x", "float")]
        )
        assert descs["x"] == ""


# ── @code → Examples doctests + paragraph grouping (gh: docstrings) ──────────
class TestCodeExamples:
    def test_code_block_captured_as_examples(self):
        raw = (
            "/**\n"
            " * @brief Do a thing.\n"
            " * @code\n"
            " * >>> from pkg import Obj\n"
            " * >>> Obj().go()\n"
            " * 42\n"
            " * @endcode\n"
            " */"
        )
        b = parse_doxygen_block(raw, name="go")
        assert b is not None
        assert b.examples == [
            ">>> from pkg import Obj",
            ">>> Obj().go()",
            "42",
        ]
        assert b.brief == "Do a thing."

    def test_code_only_block_is_not_trivial(self):
        # a block whose only content is a @code example must survive even when
        # the brief matches the name-template
        raw = (
            "/**\n * @brief go.\n * @code\n"
            " * >>> 1 + 1\n * 2\n * @endcode\n */"
        )
        b = parse_doxygen_block(raw, name="go")
        assert b is not None
        assert b.examples == [">>> 1 + 1", "2"]

    def test_render_returns_examples_and_paragraphs(self):
        raw = (
            "/**\n"
            " * @brief Summary line.\n"
            " *\n"
            " * First paragraph line one\n"
            " * line two of the same paragraph.\n"
            " * @code\n"
            " * >>> 1\n * 1\n"
            " * @endcode\n"
            " */"
        )
        b = parse_doxygen_block(raw, name="m")
        summary, body, _descs, _ret, examples = render_numpy_method_doc(b, [])
        assert summary == "Summary line."
        # multi-line prose collapses into one flowing paragraph (no per-line
        # double-spacing)
        assert body == [
            "First paragraph line one line two of the same paragraph."
        ]
        assert examples == [">>> 1", "1"]


class TestBackslashCommands:
    """gh-650: ``\\brief`` and ``@brief`` are the same Doxygen command."""

    def test_backslash_block_commands_parse(self):
        raw = (
            "/**\n"
            " * \\brief Compute the thing.\n"
            " *\n"
            " * \\param gain  Linear gain.\n"
            " * \\return The result.\n"
            " */"
        )
        b = parse_doxygen_block(raw)
        assert b is not None
        # Previously: brief kept the literal "\brief" and params/returns were
        # empty — every parameter description in the header was lost.
        assert b.brief == "Compute the thing."
        assert b.params == [("gain", "Linear gain.")]
        assert b.returns == "The result."

    def test_backslash_code_block(self):
        raw = "/**\n * \\brief Hi.\n * \\code\n * >>> 1\n * 1\n"
        raw += " * \\endcode\n */"
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.examples == [">>> 1", "1"]


class TestParamDirection:
    """gh-650: ``@param[out] name  doc`` keeps the doc and the direction."""

    def test_direction_specifiers_are_parsed(self):
        raw = (
            "/**\n"
            " * @brief Compute the thing.\n"
            " * @param[in]     x   Input sample.\n"
            " * @param[out]    y   Output sample.\n"
            " * @param[in,out] st  Running state.\n"
            " */"
        )
        b = parse_doxygen_block(raw)
        assert b is not None
        # Previously _PARAM_RE rejected the leading '[' and the handler
        # dropped the line outright — all three descriptions vanished.
        assert b.params == [
            ("x", "Input sample."),
            ("y", "Output sample."),
            ("st", "Running state."),
        ]
        assert b.param_dirs == {"x": "in", "y": "out", "st": "in,out"}

    def test_plain_param_records_no_direction(self):
        b = parse_doxygen_block("/**\n * @param x  Doc.\n */")
        assert b is not None
        assert b.params == [("x", "Doc.")]
        assert b.param_dirs == {}


class TestUnknownTagQuarantine:
    """gh-641: an unrecognized command is a command, not prose.

    The old parser had no notion of "a tag I don't handle", so such a line
    extended whichever field was open. The damage therefore depended on
    position and on whitespace — the same ``@note`` could land in the summary,
    the body, or the return description.
    """

    def test_does_not_contaminate_returns(self):
        raw = (
            "/**\n"
            " * @brief Filter one sample.\n"
            " * @param x  Input sample.\n"
            " * @return   The filtered sample.\n"
            " * @note  Not reentrant.\n"
            " * @warning Overflows above unity.\n"
            " * @see demo_reset\n"
            " */"
        )
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.returns == "The filtered sample."
        assert b.params == [("x", "Input sample.")]
        assert b.tags == [
            ("note", "Not reentrant."),
            ("warning", "Overflows above unity."),
            ("see", "demo_reset"),
        ]

    def test_does_not_contaminate_brief(self):
        raw = "/**\n * @brief Do it.\n * @note Not thread safe.\n */"
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.brief == "Do it."
        assert b.tags == [("note", "Not thread safe.")]

    def test_does_not_leak_into_body_after_blank_line(self):
        # The whitespace-dependent variant reported in gh-641.
        raw = (
            "/**\n"
            " * @brief Decimate a block.\n"
            " *\n"
            " * @note Input amplitude is bounded. A component beyond\n"
            " * the range is clipped before filtering.\n"
            " *\n"
            " * @param x input block\n"
            " */"
        )
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.body == []
        assert b.params == [("x", "input block")]
        # The continuation line joins the tag, not the body.
        assert b.tags == [
            (
                "note",
                "Input amplitude is bounded. A component beyond the range"
                " is clipped before filtering.",
            )
        ]

    def test_inline_math_is_not_a_command(self):
        # `@f$ ... @f$` would read as a command `f` with argument `$ ...`
        # without the whitespace-or-EOL requirement after the command name.
        # That invariant is `tags == []`; the text itself is now mapped to the
        # reST `:math:` role (gh-652), which is a rendering decision and not a
        # parse one. Asserting the mapped text here would re-pin the proxy, so
        # the command check is asserted directly instead.
        raw = "/**\n * @brief Gain: @f$ 20*log10(g) @f$.\n */"
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.tags == []
        assert b.brief == "Gain: :math:`20*log10(g)`."

    def test_line_leading_inline_reference_is_prose(self):
        raw = (
            "/**\n"
            " * @brief Filter one sample.\n"
            " *\n"
            " * @ref demo_reset is the counterpart.\n"
            " */"
        )
        b = parse_doxygen_block(raw)
        assert b is not None
        assert b.body == ["demo_reset is the counterpart."]
        assert b.tags == []

    def test_tags_alone_are_not_content(self):
        # Nothing renders a quarantined tag yet, so a block carrying only one
        # still falls back to the name-based stub. gh-652 flips this.
        raw = "/**\n * @note Only a note here.\n */"
        assert parse_doxygen_block(raw) is None


class TestRenderNumpyDoc:
    """gh-651: the one member-docstring renderer, and its two fallbacks."""

    def _block(self):
        return parse_doxygen_block(
            "/**\n"
            " * @brief Scale a sample.\n"
            " *\n"
            " * Extended description here.\n"
            " *\n"
            " * @param x  Sample to scale.\n"
            " * @return The scaled sample.\n"
            " */"
        )

    def test_full_layout(self):
        out = render_numpy_doc(
            self._block(), "scale", [("x", "float")], "float"
        )
        assert out[0] == '        """Scale a sample.'
        assert "        Extended description here." in out
        assert "        x : float" in out
        assert "            Sample to scale." in out
        assert "            The scaled sample." in out
        assert out[-1] == '        """'
        # No section separator carries trailing whitespace.
        assert all(ln == ln.rstrip() for ln in out)

    def test_indent_is_honoured(self):
        out = render_numpy_doc(
            self._block(), "scale", [("x", "float")], "float", indent=4
        )
        assert out[0] == '    """Scale a sample.'
        assert "    x : float" in out

    def test_undocumented_collapses_by_default(self):
        # What the module-aggregated .pyi has always done.
        out = render_numpy_doc(None, "do_thing", [("x", "float")], "float")
        assert out == ['        """Do thing."""']

    def test_undocumented_keeps_skeleton_when_asked(self):
        # What a standalone object's .pyi has always done.
        out = render_numpy_doc(
            None,
            "do_thing",
            [("x", "float")],
            "float",
            skeleton_fallback=True,
        )
        assert out[0] == '        """do_thing.'
        assert "        x : float" in out
        assert "            Input." in out
        assert "            Output." in out

    def test_override_outranks_brief(self):
        out = render_numpy_doc(
            self._block(), "scale", [], "None", override="From the manifest."
        )
        assert out[0] == '        """From the manifest.'

    def test_none_return_suppresses_returns_section(self):
        out = render_numpy_doc(self._block(), "scale", [], "None")
        assert not any("Returns" in ln for ln in out)


class TestGh678DescriptionsWrap:
    """@param / @return descriptions wrap on the same rule as the body.

    They used to be emitted verbatim however long they were, so one docstring
    could carry a wrapped summary directly above a 110-column parameter
    description -- in the generated `.pyi`, a file a reader opens and no
    formatter ever touches.
    """

    _LONG = (
        "/**\n"
        " * @brief X.\n"
        " * @param q  A genuinely long authored description that comfortably "
        "exceeds the column budget and ought to wrap somewhere sensible.\n"
        " * @return Another long authored return description that also runs "
        "well past the seventy nine column limit used here.\n"
        " */"
    )

    def _render(self, raw, params, ret):
        blk = parse_doxygen_block(raw)
        return render_numpy_doc(blk, "f", params, ret)

    def test_no_line_exceeds_the_budget(self):
        for line in self._render(self._LONG, [("q", "float")], "float"):
            assert len(line) <= 79, line

    def test_description_text_survives_the_wrap(self):
        out = " ".join(
            self._render(self._LONG, [("q", "float")], "float")
        ).split()
        assert "comfortably" in out and "sensible." in out
        assert "seventy" in out and "here." in out

    def test_a_long_token_overflows_rather_than_splitting(self):
        # Breaking a URL mid-token would make it wrong, not merely long.
        url = "https://example.com/a/" + "x" * 70
        raw = f"/**\n * @brief X.\n * @param u  See {url}\n */"
        out = self._render(raw, [("u", "str")], "None")
        assert any(url in ln for ln in out)

    def test_short_descriptions_are_unchanged(self):
        # The common case must not gain a wrap or lose its single line.
        raw = "/**\n * @brief X.\n * @param q  Input sample.\n */"
        out = self._render(raw, [("q", "float")], "None")
        assert "            Input sample." in out
