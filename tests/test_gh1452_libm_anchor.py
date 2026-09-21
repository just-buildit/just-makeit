"""gh-1452: the libm preamble is inserted into the root CMakeLists or jm
refuses to start.

The first version marked the insertion point with `#<<libm_preamble>>` in a
comment. cmake-format -- a pre-commit hook on this very template -- joined
that comment onto the line above, the `.replace()` matched nothing, and the
combined library silently linked no libm at all: the fix doing nothing,
under a green suite, until a commit reformatted the file.

So it anchors on CODE, and a missing anchor raises at import. Pure-Python,
so it runs on the fast isolated path, where a template regression should be
caught first.

GATE: the root CMakeLists resolves libm before the combined library links
      it, and a template that loses the anchor fails loudly.
"""

from __future__ import annotations

import pytest

from just_makeit import _render as R


def test_the_rendered_root_resolves_libm_before_linking_it():
    t = R.CMAKE_LISTS_TOP
    assert "find_library(JM_MATH_LIBRARY m)" in t
    assert t.index("project(") < t.index("find_library(JM_MATH_LIBRARY")
    assert t.index("find_library(JM_MATH_LIBRARY") < t.index(
        R._TOP_LIBM_ANCHOR
    )


def test_a_template_without_the_anchor_fails_loudly():
    with pytest.raises(RuntimeError, match="gh-1452"):
        R._insert_libm_preamble("cmake_minimum_required(VERSION 3.16)\n")


def test_a_duplicated_anchor_fails_loudly():
    """Exactly one: two would insert the preamble in the wrong place."""
    twice = R._TOP_LIBM_ANCHOR + "\n" + R._TOP_LIBM_ANCHOR + "\n"
    with pytest.raises(RuntimeError, match="gh-1452"):
        R._insert_libm_preamble(twice)
