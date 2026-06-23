"""Unit tests for just_makeit._docstring (Doxygen → numpy derivation)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    DoxyBlock,
    extract_doc_blocks,
    parse_doxygen_block,
    render_numpy_method_doc,
)


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
