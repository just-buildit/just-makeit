"""gh-823: an apply-time warning says whether it fails the gate.

Every warning `jm apply` printed looked identical — same prefix, same weight,
in whatever order the work reached them. Most are advisory: apply is about to
fix the thing, or the difference is one only the author can settle and no
command clears. Two are not; they are the conditions `jm status --check`
counts as drift.

That distinction cost the reporting project months. The warning naming the
drifted constructor was correct and printed on every single apply — inside a
block of a dozen warnings about fragments that were fine. Nobody was careless;
the signal had no more weight than its neighbours.

`!` gates, `~` does not — the same marks `jm status` uses in its listings — and
a trailer counts the gating ones, because a count survives a long scroll when
individual lines do not.

The honest test for `gates=True` is whether the condition reaches
`drift_count`, not whether it feels important: a mark on something that does
not fail the gate teaches the reader to ignore the mark, which is the failure
this exists to remove. These tests pin that both marks are used, and that the
two currently-gating conditions are the ones marked.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _report


@pytest.fixture(autouse=True)
def _clean():
    _report.reset()
    yield
    _report.reset()


class TestTheMarks:
    def test_advisory_is_marked(self):
        buf = io.StringIO()
        _report.warn("something", stream=buf)
        assert buf.getvalue().startswith("warning ~: ")

    def test_gating_is_marked_differently(self):
        buf = io.StringIO()
        _report.warn("something", gates=True, stream=buf)
        assert buf.getvalue().startswith("warning !: ")

    def test_the_two_are_distinguishable(self):
        """The whole point — if these rendered alike the feature is absent."""
        a, b = io.StringIO(), io.StringIO()
        _report.warn("x", stream=a)
        _report.warn("x", gates=True, stream=b)
        assert a.getvalue() != b.getvalue()

    def test_an_indented_warning_keeps_its_mark(self):
        buf = io.StringIO()
        _report.warn("x", gates=True, stream=buf, indent="  ")
        assert buf.getvalue().startswith("  warning !: ")


class TestTheCount:
    def test_only_gating_warnings_are_counted(self):
        buf = io.StringIO()
        _report.warn("a", stream=buf)
        _report.warn("b", gates=True, stream=buf)
        _report.warn("c", stream=buf)
        assert _report.gating_count() == 1

    def test_the_trailer_reports_the_count(self):
        buf = io.StringIO()
        _report.warn("b", gates=True, stream=buf)
        _report.trailer(stream=buf)
        assert "1 of the warning(s) above fail" in buf.getvalue()

    def test_a_clean_run_gains_no_noise(self):
        """Silence when nothing gates — the trailer must not become one more
        line to scroll past."""
        buf = io.StringIO()
        _report.warn("advisory only", stream=buf)
        _report.trailer(stream=buf)
        assert "fail" not in buf.getvalue()

    def test_reset_clears_it(self):
        """`apply` resets per run; a process applying twice must not
        accumulate, and the suite applies constantly."""
        _report.warn("b", gates=True, stream=io.StringIO())
        _report.reset()
        assert _report.gating_count() == 0


class TestWhichConditionsGate:
    """Pinned against the real emitters, not the helper."""

    def _apply_stderr(self, root: Path) -> str:
        from just_makeit._apply import run as apply_run

        err = io.StringIO()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            apply_run(root)
        return err.getvalue()

    @pytest.fixture()
    def drifted(self, tmp_path) -> Path:
        """A constructor whose param gained a default the fragment lacks."""
        from just_makeit._module import run as module_run
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            module_run(root, "m")
            object_run(
                root,
                "thing",
                "m",
                no_state=True,
                no_step=True,
                init_params=[
                    ("fs", "double", "", "", "", "", False, "", True),
                    ("n", "size_t", "", "", "", "", False, "", True),
                ],
            )
        old = 'name = "n"\ntype = "size_t"\nrequired = true'
        new = 'name = "n"\ntype = "size_t"\ndefault = "1024"'
        for toml in root.rglob("*.toml"):
            text = toml.read_text()
            if old in text:
                toml.write_text(text.replace(old, new))
                break
        else:
            raise AssertionError("fixture did not find the init_param block")
        return root

    def test_kwargs_drift_is_marked_gating(self, drifted):
        """It reaches `drift_count` since gh-823, so `--check` fails on it."""
        err = self._apply_stderr(drifted)
        assert "warning !:" in err
        assert "constructor's keyword arguments" in err

    def test_and_the_trailer_says_so(self, drifted):
        err = self._apply_stderr(drifted)
        assert "fail `jm status --check`" in err

    def test_a_clean_project_warns_about_nothing_gating(self, tmp_path):
        from just_makeit._new import run as new_run
        from just_makeit._object import run as object_run

        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            object_run(
                root,
                "w",
                None,
                state_vars=[("g", "double", "1.0")],
                arg_type="float",
                return_type="float",
            )
        err = self._apply_stderr(root)
        assert "warning !:" not in err
        assert "fail `jm status --check`" not in err
