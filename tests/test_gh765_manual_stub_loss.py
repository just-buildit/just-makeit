"""gh-765 — jm refuses to overwrite a hand-written stub with the placeholder.

A ``manual_stub = true`` member's ``.pyi`` text is the author's, preserved
verbatim across regenerations. Once, in ~14 identical runs of ``jm apply`` on
doppler, two of them came back as the bare ``<<MANUAL_STUB>>`` placeholder
instead — same code, same input, and six fixed ``PYTHONHASHSEED`` values did
not reproduce it.

So this file does not test a cause. The trigger is order- or
environment-dependent and may never be found; what it has is a *signature* at
the end, and the durable answer is to refuse the write when that signature
appears. These tests drive the outcome directly — a splice that fails to
recognise the member, which is what every candidate cause reduces to — and
assert that jm raises instead of writing.

The stub is jm-owned and drift-gated, so the loss cannot be repaired
downstream: restoring the text by hand makes ``jm status`` report drift and
the next ``apply`` strips it again. That is why the refusal is an exception
and not a warning.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _stubs as S  # noqa: E402

PLACEHOLDER = (
    '        """<<MANUAL_STUB>> hand-write this signature/docstring in the '
    '.pyi — jm preserves it verbatim on future regens."""'
)

# What the author wrote, and what jm renders when it does not know better.
HAND = '''\
class Resampler:
    def execute_ctrl_max_out(self) -> int:
        """Max samples ``execute_ctrl()`` can emit, for any input block."""
        ...
'''

FRESH = f"""\
class Resampler:
    def execute_ctrl_max_out(self, *args: Any, **kwargs: Any) -> Any:
{PLACEHOLDER}
"""

# `manual_stub` declared, so the splice knows the member is hand-owned.
CFG = {
    "resample": {
        "class_name": "Resampler",
        "methods": [{"name": "execute_ctrl_max_out", "manual_stub": True}],
    }
}


class TestTheRenderIsUnchanged:
    """The placeholder text moved behind a constant. If that edit changed a
    single byte, every existing stub in every project reads as drift — so the
    fixture above is checked against what jm actually emits rather than
    against what the diff looked like."""

    def test_the_fixture_matches_what_jm_renders(self):
        rendered = S._MANUAL_STUB_PLACEHOLDER
        assert rendered == "<<MANUAL_STUB>>"
        assert rendered in PLACEHOLDER


class TestTheLossIsRefused:
    def test_a_splice_that_loses_the_body_raises(self):
        """The mechanism, reduced: the splice does not recognise the member
        as hand-owned — an empty cfg is the same state a manifest fragment
        read mid-write would produce — so the fresh render's placeholder
        survives and real content is gone."""
        with pytest.raises(ValueError) as exc:
            S._splice_manual_stub_bodies({}, HAND, FRESH)
        assert "Resampler.execute_ctrl_max_out" in str(exc.value)
        assert "gh-765" in str(exc.value)

    def test_the_normal_path_still_transplants(self):
        """Guard: refusing harder must not break the case that works. With
        the manifest entry present the member is recognised and carried."""
        out = S._splice_manual_stub_bodies(CFG, HAND, FRESH)
        assert "Max samples ``execute_ctrl()`` can emit" in out
        assert "<<MANUAL_STUB>>" not in out

    def test_the_guard_is_what_raises_not_the_splice(self):
        """If `_splice_hand_owned` itself raised, the test above would pass
        for the wrong reason and the guard could be deleted unnoticed."""
        out = S._splice_hand_owned({}, HAND, FRESH)
        assert "<<MANUAL_STUB>>" in out, (
            "the unguarded splice must still produce the loss — otherwise "
            "these tests are not exercising the guard"
        )


class TestItDoesNotCryWolf:
    def test_a_first_render_is_silent(self):
        """No prior stub: the placeholder is correct, not a regression."""
        assert S._splice_manual_stub_bodies({}, "", FRESH) == FRESH

    def test_a_newly_declared_manual_stub_is_silent(self):
        """The member is new — it never had content to lose."""
        old = "class Resampler:\n    def other(self) -> int: ...\n"
        new = FRESH + "    def other(self) -> int: ...\n"
        assert S.placeholder_regressions(old, new) == []

    def test_a_still_unfilled_placeholder_is_silent(self):
        """The author has not written it yet. Placeholder in, placeholder
        out — nothing lost."""
        assert S.placeholder_regressions(FRESH, FRESH) == []


class TestItIsKeyedOnTheClassNotTheName:
    """Two classes in one stub can both declare `execute`. Keyed on the bare
    name, a legitimately-still-placeholder member on one class vouches for a
    member on the other that just lost its content — the guard fails open on
    exactly the file most likely to have the collision.
    """

    def test_a_sibling_placeholder_does_not_vouch(self):
        old = f'''\
class Fir:
    def execute(self, *args: Any, **kwargs: Any) -> Any:
{PLACEHOLDER}


class Resampler:
    def execute(self) -> int:
        """Real content the author wrote."""
        ...
'''
        new = f"""\
class Fir:
    def execute(self, *args: Any, **kwargs: Any) -> Any:
{PLACEHOLDER}


class Resampler:
    def execute(self, *args: Any, **kwargs: Any) -> Any:
{PLACEHOLDER}
"""
        # Fir.execute is a placeholder on both sides and must not be
        # reported; Resampler.execute must be.
        assert S.placeholder_regressions(old, new) == ["Resampler.execute"]
