"""gh-752: authored ``@code`` gets 8 fewer columns than its header shows.

An author writes an example inside a C comment and wraps it to the header's
79 columns, which is what their C style enforces. jm strips the ``` * ```
decoration and re-indents the line into a docstring, so a line that is 77
columns and *correct* in the header arrives at 82 in the stub. 160 of
doppler's 164 remaining over-79 lines are exactly this, over by the indent or
less.

The author cannot satisfy both rules by following either one, and the budget
they need to hit is not visible from where they are standing. So jm reports
it, per site, with the measured column count — and never edits the line: a
``>>>`` is executable and its trailing comment column is a deliberate choice.

The design decision under test here is **measure, don't predict**. Predicting
from the header meant guessing which blocks surface and at what indent, and
the guess was wrong three ways on doppler (a ``create`` block renders into the
*class* docstring at indent 4; a ``manual_stub`` member renders no docstring
at all; a module free function lives in a different header). Reading the
emitted stub makes false positives structurally impossible and covers every
producer, including the ones gh-747 tracks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _codecheck  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    example_budget,
    example_overflows,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# 74 content columns: correct in a 79-col header once ` * ` is counted,
# and 82 in a stub once jm indents it by 8.
_WIDE = ">>> obj.step(1.0)" + " " * 8 + "# " + "a" * 47
assert len(_WIDE) == 74, len(_WIDE)


class TestBudget:
    """The number an author cannot see from the header."""

    def test_a_class_member_gets_71(self):
        assert example_budget(8) == 71

    def test_a_module_level_function_gets_75(self):
        assert example_budget(4) == 75

    def test_a_line_that_fits_is_not_reported(self):
        assert example_overflows([">>> x = 1"], 8) == []

    def test_the_reported_count_is_what_it_will_occupy(self):
        ((line, cols),) = example_overflows([_WIDE], 8)
        assert line == _WIDE
        assert cols == 8 + len(_WIDE) == 82

    def test_a_blank_example_line_is_ignored(self):
        assert example_overflows(["", "   "], 8) == []


def _project(tmp_path, example_lines):
    """A component whose `step()` carries an authored ``@code`` block."""
    root = tmp_path / "proj"
    new_run("proj", root)
    object_run(root, "widget", None, state_vars=[("gain", "double", "1.0")])
    h = root / "native" / "inc" / "widget" / "widget_core.h"
    text = h.read_text(encoding="utf-8")
    marker = "widget_steps("
    idx = text.index(marker)
    start = text.rfind("/**", 0, idx)
    end = text.index("*/", start) + 2
    body = "\n".join(f" * {ln}" for ln in example_lines)
    block = f"/**\n * @brief Process a block.\n *\n * @code\n{body}\n * @endcode\n */"
    h.write_text(text[:start] + block + text[end:], encoding="utf-8")
    apply_run(root)
    return root


class TestScanMeasuresTheStub:
    """Reported lines are read out of the emitted `.pyi`, not predicted."""

    def test_a_wide_authored_example_is_reported(self, tmp_path, capsys):
        root = _project(tmp_path, [_WIDE])
        capsys.readouterr()
        found = _codecheck.scan(root, C.load(root))
        assert found, "the overlong authored example was not reported"
        ov = found[0]
        assert ov.line == _WIDE
        assert ov.columns > 79
        assert ov.budget == 79 - (ov.columns - len(ov.line))
        assert ov.symbol.startswith("widget")
        assert ov.header.name == "widget_core.h"

    def test_a_narrow_authored_example_is_not_reported(self, tmp_path):
        root = _project(tmp_path, [">>> obj.step(1.0)", "0.0"])
        assert _codecheck.scan(root, C.load(root)) == []

    def test_the_reported_line_really_is_in_the_stub(self, tmp_path):
        """Measure-don't-predict, stated as an assertion.

        Every reported line must be findable, over-79, in a generated stub.
        A predicted scan cannot promise this; that is the whole point.
        """
        root = _project(tmp_path, [_WIDE])
        stub_lines = {
            ln.strip()
            for p in (root / "src").rglob("*.pyi")
            for ln in p.read_text(encoding="utf-8").split("\n")
            if len(ln) > 79
        }
        for ov in _codecheck.scan(root, C.load(root)):
            assert ov.line in stub_lines, ov.line

    def test_jm_never_rewrites_the_authored_line(self, tmp_path):
        """Reporting, not repairing — the example must survive verbatim."""
        root = _project(tmp_path, [_WIDE])
        text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (root / "src").rglob("*.pyi")
        )
        assert _WIDE in text

    def test_the_description_names_site_columns_and_budget(self, tmp_path):
        root = _project(tmp_path, [_WIDE])
        (ov, *_) = _codecheck.scan(root, C.load(root))
        msg = ov.describe(root)
        assert "widget_core.h" in msg
        assert str(ov.columns) in msg
        assert str(ov.budget) in msg
        assert "native/inc" in msg, "the path should be project-relative"


class TestReporting:
    """What the author actually sees."""

    def test_apply_warns_and_names_the_budget(self, tmp_path, capsys):
        root = _project(tmp_path, [_WIDE])
        capsys.readouterr()
        apply_run(root)
        out = capsys.readouterr().out
        assert "@code line(s) will exceed 79 columns" in out
        assert "wrap at <=" in out

    def test_a_clean_project_says_nothing(self, tmp_path, capsys):
        root = _project(tmp_path, [">>> obj.step(1.0)", "0.0"])
        capsys.readouterr()
        apply_run(root)
        assert "@code" not in capsys.readouterr().out

    def test_report_returns_the_full_count_even_when_truncated(
        self, tmp_path, capsys
    ):
        """A truncated listing must not read as a small problem."""
        root = _project(tmp_path, [_WIDE, _WIDE[:-1] + "b", _WIDE[:-1] + "c"])
        capsys.readouterr()
        n = _codecheck.report(root, C.load(root), limit=1)
        out = capsys.readouterr().out
        assert n == 3
        assert "3 authored" in out
        assert "and 2 more" in out

    def test_status_reports_a_count_without_calling_it_drift(
        self, tmp_path, capsys
    ):
        from just_makeit import _status

        root = _project(tmp_path, [_WIDE])
        capsys.readouterr()
        rc = _status.run(root, check=True)
        out = capsys.readouterr().out
        assert rc == 0, "an over-wide example is not drift"
        assert "exceed 79 columns" in out
        assert "Not drift" in out


@pytest.mark.parametrize("indent,budget", [(8, 71), (4, 75)])
def test_budget_matches_the_documented_table(indent, budget):
    """The two figures doppler asked to be told, per site."""
    assert example_budget(indent) == budget
