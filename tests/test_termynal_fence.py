"""The termynal superfence — colorised terminal transcripts on the docs site.

Covers the `{jm_version}` token in particular: an install transcript that
hard-codes a version goes stale silently (the homepage shipped `0.29.0` well
into the 0.30 line). The fence substitutes the token with the installed
version at build time instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import __version__
from just_makeit._termynal_fence import termynal_fence


def _render(source: str) -> str:
    # The signature mkdocs/pymdownx calls custom fences with; only `source`
    # matters here.
    return termynal_fence(source, "termynal", "termynal", {}, md=None)


class TestVersionToken:
    def test_token_is_replaced_with_installed_version(self):
        out = _render("$ pip show just-makeit\n  version {jm_version}")
        assert __version__ in out
        assert "{jm_version}" not in out

    def test_no_token_leaves_output_unchanged(self):
        # A transcript without the token must not gain a version from nowhere.
        out = _render("$ echo hello\nhello")
        assert "{jm_version}" not in out
        assert "hello" in out


class TestRendering:
    def test_input_line_animates_as_typed(self):
        out = _render("$ jm new proj")
        assert 'data-ty="input"' in out
        assert "jm new proj" in out

    def test_comment_line(self):
        out = _render("# a note")
        assert 'data-ty="comment"' in out

    def test_color_markup_becomes_span(self):
        # Text on both sides of the marker exercises the pre-marker and
        # trailing-text escape branches, not just the span itself.
        out = _render("built {g}ok{/g} now")
        assert 'class="ty-green-bold"' in out
        assert ">ok<" in out
        assert "built " in out
        assert " now" in out
        # The markup delimiters themselves are consumed, not shown.
        assert "{g}" not in out
        assert "{/g}" not in out

    def test_html_is_escaped(self):
        # A literal '<' in transcript text must not open an HTML tag.
        out = _render("$ echo a < b")
        assert "&lt;" in out
        assert "a < b" not in out

    def test_blank_line_becomes_a_gap(self):
        # A blank line in a transcript renders a spacer span, not nothing.
        out = _render("$ one\n\n$ two")
        assert "<span data-ty>&nbsp;</span>" in out
