"""gh-1261: a wrapped `@param` description lost its continuation line.

`reconcile_param_docs` (gh-1098) captured each `@param` with a purely
line-based scan: one dict entry per matched `@param NAME ...` line. Under the
79-column style a description commonly wraps onto a following physical line —
`* @param x  some words that\n*            continue here.` — and that
continuation line does not itself match `@param`, so it fell into "everything
else, left in place" at its OLD position. Once the `@param` lines were
re-emitted as one contiguous group elsewhere, the orphaned continuation was
left behind, landing next to whatever `@param`/`@return` happened to end up
adjacent to its old position — silently attaching one parameter's prose to a
different one, or leaving it dangling after the whole group.

Found adopting jm 0.75.1 in doppler: gh-1246's `complex` -> `_Complex`
respelling routes nearly every prototype through `_reconcile_decl_doc`, and
`reconcile_param_docs` runs on EVERY such refresh regardless of whether the
`@param` set itself needs to change — so this fired even where the parameter
list was byte-identical before and after, corrupting real published API docs
in `snr_core.h`, `mpsk_core.h`, `wfm_reader_core.h` and others.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import reconcile_param_docs  # noqa: E402


def _block(
    *param_lines: str, brief: str = "Do a thing.", ret: str = ""
) -> str:
    lines = ["/**", f" * @brief {brief}", " *"]
    lines.extend(param_lines)
    if ret:
        lines.append(f" * @return {ret}")
    lines.append(" */")
    return "\n".join(lines)


class TestAWrappedDescriptionSurvives:
    def test_unchanged_params_leave_the_block_byte_identical(self):
        """The doppler case: only the TYPE spelling changed on the decl, so
        `x`/`x_len` are identical before and after — the block must not move
        at all, let alone lose a line."""
        block = _block(
            " * @param x      Complex baseband samples (post-carrier-lock;"
            " residual",
            " *               phase does not bias the moment-based estimate).",
            " * @param x_len  Length of @p x.",
            ret="Es/N0 in dB.",
        )
        decl = "double snr_m2m4_db(const float _Complex *x, size_t x_len);"
        assert reconcile_param_docs(block, decl) == block

    def test_the_continuation_travels_with_its_param_when_reordered(self):
        """Reordering IS supposed to happen here (gh-1098's whole feature) —
        the continuation line must move as a unit with its owning @param,
        not stay behind at its old position."""
        block = _block(
            " * @param b  second",
            " * @param a  first, with a description that wraps onto",
            " *          a second physical line.",
        )
        decl = "void f(int a, int b);"
        out = reconcile_param_docs(block, decl)
        a_at = out.index("@param a")
        b_at = out.index("@param b")
        assert a_at < b_at
        assert "a second physical line." in out
        # the continuation must appear between `a`'s own line and `b`'s,
        # not after b's.
        assert a_at < out.index("a second physical line.") < b_at

    def test_a_dropped_params_continuation_is_dropped_too(self):
        """Removing `gain` must remove BOTH of its physical lines — an
        orphaned continuation surviving into the rebuilt block would attach
        stray prose to whatever `@param` ends up next to it."""
        block = _block(
            " * @param gain  Initial gain, clamped to the device's usable",
            " *              range on the next reset.",
            " * @param dwell  AUTHORED.",
        )
        decl = "void f(size_t dwell);"
        out = reconcile_param_docs(block, decl)
        assert "gain" not in out
        assert "clamped to the device" not in out
        assert " * @param dwell  AUTHORED." in out

    def test_a_new_param_inserted_between_two_wrapped_ones_is_clean(self):
        block = _block(
            " * @param a  first, wraps onto",
            " *          a continuation.",
            " * @param c  third, wraps onto",
            " *          its own continuation.",
        )
        decl = "void f(int a, int b, int c);"
        out = reconcile_param_docs(block, decl)
        a_at = out.index("@param a")
        b_at = out.index("@param b")
        c_at = out.index("@param c")
        assert a_at < b_at < c_at
        assert "a continuation." in out
        assert "its own continuation." in out
        # each continuation stays with its own param, not the new one
        assert a_at < out.index("a continuation.") < b_at
        assert c_at < out.index("its own continuation.")

    def test_it_is_idempotent(self):
        block = _block(
            " * @param x  wraps onto",
            " *          a second line.",
        )
        decl = "void f(int x);"
        once = reconcile_param_docs(block, decl)
        assert reconcile_param_docs(once, decl) == once

    def test_a_blank_comment_line_ends_the_continuation(self):
        """A blank ` *` line is a section break in Doxygen, not part of the
        previous @param's description — content after it belongs to
        `kept`, untouched, not swallowed into the param above it."""
        block = _block(
            " * @param x  short.",
            " *",
            " * Free-standing prose, unrelated to any @param.",
        )
        decl = "void f(int x);"
        out = reconcile_param_docs(block, decl)
        assert " * Free-standing prose, unrelated to any @param." in out

    def test_a_return_blocks_own_continuation_is_untouched(self):
        """`@return`'s multi-line prose is not part of this feature at all —
        it must survive exactly as written, in place."""
        block = _block(
            " * @param x  short.",
            ret="a long result that\n *         wraps onto a second line.",
        )
        decl = "void f(int x);"
        out = reconcile_param_docs(block, decl)
        assert "wraps onto a second line." in out

    def test_the_closing_marker_is_not_swallowed_as_a_continuation(self):
        """`*/`'s `/` satisfies "non-blank comment content" exactly like a
        real continuation line does. With no `@return` after the last
        `@param`, the closer is the very next line -- so the same rule that
        lets a continuation travel with its `@param` can just as easily eat
        the block's own terminator instead, attaching it to whichever
        `@param` is last. That is silent when the last param survives (it
        lands back in the same place either way) -- the actual regression
        this catches is a signature change that DROPS the last param: its
        whole entry, closing marker included, is discarded with it, deleting
        `*/` from the output and leaving everything the caller appends after
        (the very next declaration) inside a now-unterminated `/**` comment.
        This is the exact shape that broke `test_ext_c_compiles[steps]`:
        `osc_steps(state, input, output, n)` shrinking to `osc_steps(state)`.
        """
        block = (
            "/**\n"
            " * @brief Steps.\n"
            " *\n"
            " * @param state\n"
            " * @param input\n"
            " * @param output\n"
            " * @param n\n"
            " */"
        )
        decl = "double osc_steps(osc_state_t *state);"
        out = reconcile_param_docs(block, decl)
        assert out.endswith("*/")
        assert out.count("*/") == 1
