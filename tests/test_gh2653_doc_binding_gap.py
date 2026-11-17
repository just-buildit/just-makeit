"""gh-2653: what may sit between a doc block and the declaration it documents.

doppler made its headers includable from C++ and wrote this:

    /** @brief Square-clip a complex sample. ... */
    #ifdef __cplusplus
      /* From C++ this calls the out-of-line copy. */
      float _Complex dp_square_clip (float _Complex y, float lin);
    #else
      JM_FORCEINLINE float _Complex
      dp_square_clip (float _Complex y, float lin) { ... }
    #endif

`extract_doc_blocks` returned nothing for `dp_square_clip`: the binding and
the `.pyi` rendered without the Doxygen, and `make drift-check` went red with
no hint why. The binder skipped only whitespace. A preprocessor line got
through by accident (the declaration match swallowed it); an ordinary comment
did not get through at all.

Doxygen skips ordinary comments and preprocessor directives between a doc
block and its declaration, so jm now does too, through ONE declared gap
(`_DOC_GAP`) that every binder -- block, line run, struct -- concatenates.
Two things stay binding-enders, and are asserted here so the widening cannot
overshoot:

- a second DOC comment (the nearest block wins, gh-666);
- a ``#define``, which is documentable itself. The old incidental tolerance
  handed a macro's block to the function after it; that was a bug too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    extract_doc_blocks,
    extract_struct_doc_blocks,
    parse_doxygen_block,
)

_DOC = (
    "/**\n"
    " * @brief Square-clip a complex sample.\n"
    " * @param y    Input.\n"
    " * @param lin  Threshold.\n"
    " * @return Clipped sample.\n"
    " */\n"
)
_BRIEF = "Square-clip a complex sample."
_PROTO = "  float _Complex dp_square_clip (float _Complex y, float lin);\n"
_DEFN = (
    "  JM_FORCEINLINE float _Complex\n"
    "  dp_square_clip (float _Complex y, float lin) { return y; }\n"
)


def _brief(src: str, name: str = "dp_square_clip") -> str | None:
    """The parsed ``@brief`` *src* binds to *name*, or None if unbound."""
    raw = extract_doc_blocks(src).get(name)
    if raw is None:
        return None
    blk = parse_doxygen_block(raw, name=name)
    return blk.brief if blk else None


# ── the issue's three measured shapes ───────────────────────────────────────

_SHAPES = {
    # doppler's workaround order: the C definition first.
    "c_first": (
        _DOC + "#ifndef __cplusplus\n" + _DEFN + "#else\n"
        "  /* From C the inline definition above is used. */\n"
        + _PROTO
        + "#endif\n"
    ),
    "cxx_first_no_comment": (
        _DOC + "#ifdef __cplusplus\n" + _PROTO + "#else\n" + _DEFN + "#endif\n"
    ),
    # The shape that dropped the docstring.
    "cxx_first_plain_comment": (
        _DOC + "#ifdef __cplusplus\n"
        "  /* From C++ this calls the out-of-line copy. */\n"
        + _PROTO
        + "#else\n"
        + _DEFN
        + "#endif\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_issue_shapes_bind(shape: str) -> None:
    assert _brief(_SHAPES[shape]) == _BRIEF


# ── the whole class: each thing the gap skips, alone ────────────────────────

_SKIPPED = {
    "blank_lines": "\n\n\n",
    "plain_block_comment": "/* ordinary */\n",
    "plain_multiline_comment": "/*\n * ordinary\n */\n",
    "plain_line_comment": "// ordinary, with a # in it\n",
    "slash_ruler": "////////////////\n",
    "empty_block_comment": "/**/\n",
    "if_directive": "#if defined(X) && X > 1\n",
    "pragma_directive": "#pragma once\n",
    "continued_directive": "#if defined(A) \\\n    || defined(B)\n",
    "cxx_guard_then_comment": "#ifdef __cplusplus\n// for C++\n",
    "nested_conditionals": "#ifdef A\n#ifndef B\n/* x */\n",
}


@pytest.mark.parametrize("gap", sorted(_SKIPPED))
def test_gap_is_skipped(gap: str) -> None:
    src = "/** @brief Doc. */\n" + _SKIPPED[gap] + "int f(int x);\n"
    assert _brief(src, "f") == "Doc."


@pytest.mark.parametrize("gap", sorted(_SKIPPED))
def test_gap_is_skipped_after_a_line_run(gap: str) -> None:
    src = "/// @brief Doc.\n" + _SKIPPED[gap] + "int f(int x);\n"
    assert _brief(src, "f") == "Doc."


# ── what still ends the binding ─────────────────────────────────────────────


def test_second_doc_block_wins() -> None:
    """The nearest doc comment binds, even across an ordinary comment."""
    src = (
        "/** @brief Far. */\n/* plain */\n"
        "/** @brief Near. */\n/* plain */\nint f(int x);\n"
    )
    assert _brief(src, "f") == "Near."


def test_define_ends_the_binding() -> None:
    """A macro takes its block; the function after it is undocumented."""
    src = (
        "/** @brief Larger of two. */\n"
        "#define MAX(a, b) ((a) > (b) ? (a) : (b))\n"
        "int f(int x);\n"
    )
    assert extract_doc_blocks(src) == {}


def test_declaration_ends_the_binding() -> None:
    """A block binds one declaration, not the one after it."""
    src = "/** @brief Doc. */\nint f(int x);\nint g(int x);\n"
    assert sorted(extract_doc_blocks(src)) == ["f"]


# ── the struct binder skips the same gap ────────────────────────────────────


def test_struct_doc_crosses_plain_comment_and_guard() -> None:
    src = (
        "/** @brief One clip. */\n"
        "#ifndef CLIP_OPAQUE\n"
        "/* the full layout */\n"
        "typedef struct {\n    double gain;\n} clip_t;\n"
        "#endif\n"
    )
    assert extract_struct_doc_blocks(src) == {
        "clip_t": "/** @brief One clip. */"
    }


def test_struct_doc_stops_at_define() -> None:
    src = (
        "/** @brief Width. */\n#define W 4\n"
        "typedef struct {\n    double gain;\n} clip_t;\n"
    )
    assert extract_struct_doc_blocks(src) == {}
