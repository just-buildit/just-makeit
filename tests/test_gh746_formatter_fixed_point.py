"""gh-746 precondition: jm's stub output is a fixed point of a formatter.

gh-746 asks jm to run the project's pinned Python formatter over the
generated `.pyi`. Measuring that before building it turned up a conflict:
`ruff format` *raised* doppler's over-79 count from 49 to 51.

The cause is a six-column window. jm emits a compliant block whose closing
delimiter sits on its own line::

        \"\"\"Bonferroni per-cell false-alarm probability over the cells.
        \"\"\"

`ruff format` pulls that closer up. It enforces ``line-length`` on code, not
on string *content*, so it never checks what the joined line measures — and
at exactly 79 the join lands on 82. Every summary whose length falls in that
window is affected, in every file.

jm cannot stop ruff joining. It can stop *producing the shape ruff joins
badly*, which is what these tests pin: the final content line is kept three
columns short, so pulling the delimiter up is safe whether or not anything
ever does it.

That is a real invariant even with gh-746 unbuilt — today nothing formats the
stubs, so the old shape was compliant and stable. It only became wrong the
moment a formatter was pointed at it, which is precisely what gh-746 does.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    STUB_TARGET_WIDTH,
    summary_docstring,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

# The measured doppler case: 68 characters, which fits the opening line at
# indent 8 (8 + 3 + 68 = 79) but not once the closer is pulled up (82).
_WINDOW_CASE = (
    "Bonferroni per-cell false-alarm probability over the searched cells."
)


def _closer_pulled_up(block: list[str]) -> int:
    """Columns of the last content line once a formatter appends the closer."""
    return len(block[-2]) + 3


class TestCloserCanBePulledUpSafely:
    """The invariant, stated directly."""

    def test_the_measured_doppler_case(self):
        block = summary_docstring(_WINDOW_CASE, indent=8)
        assert max(len(ln) for ln in block) <= STUB_TARGET_WIDTH
        assert _closer_pulled_up(block) <= STUB_TARGET_WIDTH

    @pytest.mark.parametrize("indent", [4, 8])
    @pytest.mark.parametrize("length", range(40, 110))
    def test_across_the_whole_window(self, indent, length):
        """Sweep every summary length that could land in the window.

        The bug lived in a six-column band, so a single example proves very
        little — an off-by-one in the budget would pass the case above and
        fail three characters either side of it.
        """
        text = ("word " * 40)[:length].strip()
        block = summary_docstring(text, indent=indent)
        assert max(len(ln) for ln in block) <= STUB_TARGET_WIDTH, block
        if len(block) > 1:  # a one-liner already carries both delimiters
            assert _closer_pulled_up(block) <= STUB_TARGET_WIDTH, block

    def test_a_short_summary_still_gets_the_one_line_form(self):
        """No churn: the common case must not grow a line."""
        assert summary_docstring("Sample rate in Hz.", indent=8) == [
            '        """Sample rate in Hz."""'
        ]

    def test_an_unsplittable_token_is_left_rather_than_lied_about(self):
        """A single long word cannot be made to fit; do not pretend."""
        block = summary_docstring("x" * 200, indent=8)
        assert "x" * 200 in "".join(block)


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
class TestAgainstRuffItself:
    """The property that matters, asserted against the real formatter."""

    def test_ruff_never_collapses_a_docstring_over_the_target(self, tmp_path):
        """The precondition, end-to-end against the real formatter.

        Not full byte-identity — `ruff format` also applies PEP 484 stub
        style (it drops the blank line jm emits between two simple stub
        defs), and reconciling *that* is gh-746's actual deliverable. What
        must hold **before** gh-746 can be built is narrower and is the thing
        that was broken: no docstring may come back over the target.
        """
        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(
            root,
            "widget",
            None,
            state_vars=[("gain", "double", "1.0")],
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        property_run(
            root, "widget", "source", None, "int", False, doc=_WINDOW_CASE
        )
        apply_run(root)

        stubs = sorted((root / "src").rglob("*.pyi"))
        assert stubs, "nothing generated to check"
        proc = subprocess.run(
            ["ruff", "format", "--line-length", str(STUB_TARGET_WIDTH)]
            + [str(p) for p in stubs],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr

        for p in stubs:
            for n, ln in enumerate(
                p.read_text(encoding="utf-8").split("\n"), 1
            ):
                if '"""' in ln:
                    assert len(ln) <= STUB_TARGET_WIDTH, (
                        f"{p.name}:{n} is {len(ln)} columns after ruff "
                        f"format — the gh-746 precondition has regressed"
                    )

    def test_ruff_does_not_push_any_line_over_the_target(self, tmp_path):
        """The 49 -> 51 regression, as an assertion."""
        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(root, "widget", None, state_vars=[("g", "double", "1.0")])
        property_run(
            root, "widget", "source", None, "int", False, doc=_WINDOW_CASE
        )
        apply_run(root)
        stubs = sorted((root / "src").rglob("*.pyi"))

        def _over(paths):
            return sum(
                1
                for p in paths
                for ln in p.read_text(encoding="utf-8").split("\n")
                if len(ln) > STUB_TARGET_WIDTH
            )

        before = _over(stubs)
        subprocess.run(
            ["ruff", "format", "--line-length", str(STUB_TARGET_WIDTH)]
            + [str(p) for p in stubs],
            capture_output=True,
            text=True,
            check=True,
        )
        assert _over(stubs) <= before
