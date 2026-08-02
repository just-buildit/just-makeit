"""gh-654: every Doxygen comment form derives, and `/**<` no longer misbinds.

`extract_doc_blocks` recognised exactly one spelling — a `/** … */` block
immediately preceding a declaration. Doxygen treats four as equivalent, and the
other three produced **no error and no documentation**: a member documented in
C and undocumented in Python, with nothing anywhere saying why.

Two things came out of probing rather than out of the issue:

- **`/**<` was matched by the opener.** A trailing member doc (gh-671)
  separated from a following declaration by whitespace alone was extracted as
  that function's block, giving it the brief `< Number of filter taps.` — the
  stray `<` reaching both faces. That is a fix, not a widening, and it is
  asserted here directly.
- **A `///` ruler is not documentation.** `////////` above a declaration is
  ordinary C house style; read literally it gives that function a brief made of
  punctuation. Ruler lines read as blanks, which is also what makes a
  ruler-wrapped doc line come out clean.

The evidence for scheduling this is recorded on the issue: doppler's 133
git-tracked own headers use `/**` 415 times and `/**<` 744 times, and the other
three forms zero times. So this closes a silent failure mode rather than
serving a measured demand — the justification is that a header jm cannot read
should not look identical to a header with no documentation in it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    _strip_comment,
    extract_doc_blocks,
    extract_member_docs,
    parse_doxygen_block,
)


def _briefs(src: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name, raw in extract_doc_blocks(src).items():
        blk = parse_doxygen_block(raw, name=name)
        out[name] = blk.brief if blk else None
    return out


# ── the four forms are one construct ────────────────────────────────────────

_FORMS = {
    "block": "/**\n * @brief Doc.\n * @param x In.\n * @return Out.\n */",
    "block_bang": "/*!\n * @brief Doc.\n * @param x In.\n * @return Out.\n */",
    "line": "/// @brief Doc.\n/// @param x In.\n/// @return Out.",
    "line_bang": "//! @brief Doc.\n//! @param x In.\n//! @return Out.",
}


@pytest.mark.parametrize("form", sorted(_FORMS), ids=sorted(_FORMS))
class TestEveryFormDerives:
    """Doxygen treats these as the same comment; so must derivation."""

    @staticmethod
    def _parsed(form: str):
        src = f"{_FORMS[form]}\nint f(int x);\n"
        raw = extract_doc_blocks(src)
        assert "f" in raw, f"the {form} form derived nothing at all"
        return parse_doxygen_block(raw["f"], name="f")

    def test_brief(self, form):
        assert self._parsed(form).brief == "Doc."

    def test_params(self, form):
        assert self._parsed(form).params == [("x", "In.")]

    def test_returns(self, form):
        assert self._parsed(form).returns == "Out."


def test_all_four_forms_parse_identically():
    """One assertion for the property the parametrization implies."""
    parsed = [
        parse_doxygen_block(
            extract_doc_blocks(f"{src}\nint f(int x);\n")["f"], name="f"
        )
        for src in _FORMS.values()
    ]
    assert len({(p.brief, tuple(p.params), p.returns) for p in parsed}) == 1


# ── the `/**<` regression ───────────────────────────────────────────────────


class TestMemberDocDoesNotMisbind:
    """A trailing member doc belongs to its own line, not the next one."""

    _SRC = "int taps;  /**< Number of filter taps. */\n\nint fir_run(void);\n"
    _SRC_LINE = "int taps;  ///< Taps.\n\nint fir_run(void);\n"

    def test_block_member_doc_is_not_the_next_declarations_doc(self):
        assert _briefs(self._SRC) == {}

    def test_line_member_doc_is_not_the_next_declarations_doc(self):
        assert _briefs(self._SRC_LINE) == {}

    def test_the_stray_angle_bracket_is_gone(self):
        """The symptom, asserted directly."""
        assert "< Number of filter taps." not in str(_briefs(self._SRC))

    def test_member_docs_still_extract(self):
        """The fix must not cost gh-671 its input."""
        got = extract_member_docs(self._SRC + self._SRC_LINE)
        assert got == {"taps": "Number of filter taps."}


# ── rulers ──────────────────────────────────────────────────────────────────


class TestRulers:
    def test_a_bare_ruler_documents_nothing(self):
        src = "//////////\n// Public API\n//////////\nint f(void);\n"
        assert _briefs(src) == {}

    def test_a_ruler_wrapped_doc_comes_out_clean(self):
        src = "//////////\n/// @brief Real doc.\n//////////\nint f(void);\n"
        assert _briefs(src) == {"f": "Real doc."}

    def test_a_plain_double_slash_is_not_a_doc_comment(self):
        assert _briefs("// int old(void);\nint f(void);\n") == {}


# ── binding ─────────────────────────────────────────────────────────────────


class TestNearestCommentWins:
    """gh-666's property, now that there is more than one spelling of it."""

    @pytest.mark.parametrize(
        "src",
        [
            "/// @brief Far.\n/** @brief Near. */\nint f(void);\n",
            "/** @brief Far. */\n/// @brief Near.\nint f(void);\n",
            "/// @brief Far.\n\n/// @brief Near.\nint f(void);\n",
            "/*! @brief Far. */\n/// @brief Near.\nint f(void);\n",
        ],
        ids=[
            "line-then-block",
            "block-then-line",
            "two-runs",
            "bang-then-line",
        ],
    )
    def test_the_nearest_comment_binds(self, src):
        assert _briefs(src) == {"f": "Near."}


# ── _strip_comment openers ──────────────────────────────────────────────────


class TestStripComment:
    def test_bang_block_opener_no_longer_leaks(self):
        """The issue's second half: `/*!` fell through to the `/*` branch."""
        assert _strip_comment("/*! @brief One. */") == ["@brief One."]

    def test_a_line_run_keeps_its_paragraph_break(self):
        got = _strip_comment("/// One.\n///\n/// Two.")
        assert got == ["One.", "", "Two."]

    def test_a_line_run_drops_exactly_one_space(self):
        assert _strip_comment("///   indented") == ["  indented"]

    def test_the_original_form_is_untouched(self):
        assert _strip_comment("/** @brief One. */") == ["@brief One."]


# ── end to end ──────────────────────────────────────────────────────────────


def test_a_line_commented_header_reaches_both_faces(tmp_path):
    """Derivation is only useful if it survives the whole pipeline."""
    from just_makeit._apply import run as apply_run
    from just_makeit._new import run as new_run
    from just_makeit._object import run as object_run

    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    header = root / "native/inc/widget/widget_core.h"
    text = header.read_text(encoding="utf-8")
    old = "@brief Process one input sample."
    assert old in text, "the scaffold no longer writes that brief"
    # Replace the whole scaffold block above step() with the `///` spelling.
    start = text.rindex("/**", 0, text.index(old))
    end = text.index("*/", text.index(old)) + 2
    text = (
        text[:start]
        + "/// @brief SENTINEL654 line-comment derivation.\n"
        + "/// @param x Input sample.\n"
        + "/// @return Output sample."
        + text[end:]
    )
    header.write_text(text, encoding="utf-8")
    apply_run(root)

    pyi = (root / "src/demo/widget.pyi").read_text(encoding="utf-8")
    ext = (root / "native/src/widget/widget_ext.c").read_text(encoding="utf-8")
    missing = [
        face
        for face, blob in (("python", pyi), ("runtime", ext))
        if "SENTINEL654" not in blob
    ]
    assert not missing, f"derived doc missing from: {', '.join(missing)}"
