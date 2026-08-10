"""gh-921: the gh-920 seam is safe, and now it is also visible.

gh-920 stopped jm reading a ``max_out(state)`` as a per-call bound. Under
``pass_capacity`` a state-only prototype keeps the defensive
``max(max_out, n)`` clamp, so a request above the cap is no longer silently
truncated. That made the seam **safe**.

It did not make it **visible**. A project in it — ``pass_capacity = true`` in
the manifest, ``max_out(state)`` in the sacred header — asked for the exact
allocation, got the historical clamped one, and nothing said so. The opt-in is
inert and the only way to notice is to read the generated glue.

``jm status`` is where that is reported, and it is reported as a NOTE:

- **not counted** in the drift total, so ``--check`` does not gate on it and
  the summary still reads "up to date" — nothing here is broken, and gh-920 is
  what makes that true;
- **not printed** under ``--check``, whose whole output is that count;
- **cannot be wrong** about a method it names — the arity is read off the
  header's own declaration and the flag off the manifest, with no inference in
  between. That is the argument for a note rather than a warning.

``status`` rather than the mutating commands: the condition is per method, and
on a tree carrying dozens of variable-output methods an ``apply``-time note is
a wall of lines arriving exactly when the reader is watching for what changed.
This is a standing property of the manifest, not an event.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _status  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import inert_pass_capacity  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402

STATE_ONLY = "size_t nco_steps_u32_max_out(nco_state_t *state)"


def _scaffold(dest: Path, *, state_only: bool, **method_over) -> Path:
    """A project with one `pass_capacity` generator, header arity as asked.

    *state_only* rewrites the sacred header's `max_out` prototype to the
    pre-gh-607 form — the author's own declaration, which is what puts the
    project in the seam. Left alone, jm scaffolds gh-607's count-bearing form.

    `jm apply` runs last, and it is load-bearing rather than tidy: rewriting
    the prototype leaves the glue rendered against the *old* arity, which is
    real STALE drift. Without the apply, "the note is not counted" would be
    measured over a tree that has other things to count, and would pass for
    the wrong reason.
    """
    from just_makeit._apply import run as apply_run

    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", dest)
        object_run(
            dest,
            "nco",
            module=None,
            state_vars=[("phase", "uint32_t", "0")],
        )
        method_run(
            dest,
            "nco",
            "steps_u32",
            None,
            "void",
            "uint32_t",
            True,
            [],
            pass_capacity=method_over.pop("pass_capacity", True),
            **method_over,
        )
        if state_only:
            header = dest / "native/inc/nco/nco_core.h"
            text = header.read_text("utf-8")
            text, n_sub = re.subn(
                r"size_t nco_steps_u32_max_out\s*\([^)]*\)",
                STATE_ONLY,
                text,
                count=1,
            )
            assert n_sub == 1, "header shape changed; update this test"
            header.write_text(text, encoding="utf-8")
        apply_run(dest)
    return dest


def _status_text(root: Path, **kw) -> "tuple[int, str]":
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _status.run(root, **kw)
    return rc, buf.getvalue()


@pytest.fixture(scope="module")
def seam(tmp_path_factory):
    """The project gh-921 is about: opted in, state-only prototype."""
    return _scaffold(
        tmp_path_factory.mktemp("gh921seam") / "p", state_only=True
    )


class TestTheDetector:
    def test_it_names_the_method_in_the_seam(self, seam):
        cfg = C.load(seam)
        assert inert_pass_capacity(cfg, seam, "nco") == [
            ("steps_u32", "nco_steps_u32")
        ]

    def test_the_count_bearing_prototype_is_not_in_the_seam(
        self, tmp_path_factory
    ):
        """gh-607's form gets the exact allocation, so there is nothing to say."""
        root = _scaffold(
            tmp_path_factory.mktemp("gh921counted") / "p", state_only=False
        )
        assert inert_pass_capacity(C.load(root), root, "nco") == []

    def test_exact_max_out_is_the_documented_way_out(self, tmp_path_factory):
        """It drops the clamp on its own, so the opt-in is not inert.

        Reporting it would tell an author who already answered the question
        that they have not.
        """
        root = _scaffold(
            tmp_path_factory.mktemp("gh921exact") / "p",
            state_only=True,
            exact_max_out=True,
        )
        assert inert_pass_capacity(C.load(root), root, "nco") == []

    def test_without_pass_capacity_there_is_no_opt_in_to_be_inert(
        self, tmp_path_factory
    ):
        root = _scaffold(
            tmp_path_factory.mktemp("gh921plain") / "p",
            state_only=True,
            pass_capacity=False,
        )
        assert inert_pass_capacity(C.load(root), root, "nco") == []

    def test_it_reports_only_the_method_in_the_seam(self, tmp_path_factory):
        """Three `pass_capacity` methods on one object, one of them inert.

        Both non-reports are their own gap rather than padding:

        - ``steps_plain`` keeps gh-607's count-bearing prototype, so the walk
          reaches the arity check, declines, and must carry on to the next
          method. A detector that stopped at the first non-match would report
          nothing at all here.
        - ``tally`` sets ``pass_capacity`` without ``variable_output``. Nothing
          couples those keys — `jm method --pass-capacity` writes it either way
          — and `_capacity_exprs` runs on the variable-output path alone, so
          there is no allocation for the opt-in to be inert about.
        """
        dest = tmp_path_factory.mktemp("gh921mixed") / "p"
        from just_makeit._apply import run as apply_run

        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(
                dest,
                "nco",
                module=None,
                state_vars=[("phase", "uint32_t", "0")],
            )
            for mname, var_out in (
                ("steps_u32", True),
                ("steps_plain", True),
                ("tally", False),
            ):
                method_run(
                    dest,
                    "nco",
                    mname,
                    None,
                    "void",
                    "uint32_t",
                    var_out,
                    [],
                    pass_capacity=True,
                )
            header = dest / "native/inc/nco/nco_core.h"
            text = header.read_text("utf-8")
            text, n_sub = re.subn(
                r"size_t nco_steps_u32_max_out\s*\([^)]*\)",
                STATE_ONLY,
                text,
                count=1,
            )
            assert n_sub == 1, "header shape changed; update this test"
            header.write_text(text, encoding="utf-8")
            apply_run(dest)

        assert inert_pass_capacity(C.load(dest), dest, "nco") == [
            ("steps_u32", "nco_steps_u32")
        ]

    def test_an_absent_header_reports_nothing(self, seam, tmp_path):
        """Best-effort in the same direction as gh-442's init-param drift.

        A false negative here costs a note; a false positive names a method
        that is fine, which is what trains a reader past the whole section.
        """
        empty = tmp_path / "no-native"
        empty.mkdir()
        assert inert_pass_capacity(C.load(seam), empty, "nco") == []


class TestTheReport:
    def test_status_prints_the_note_and_names_both_halves(self, seam):
        _, out = _status_text(seam)
        assert "NOTE (1)" in out
        assert "nco.steps_u32" in out
        assert "nco_steps_u32_max_out(state) cannot see the call" in out
        assert "max(max_out, n)" in out

    def test_it_offers_both_exits(self, seam):
        """A note nobody can act on is the failure mode this must not be."""
        _, out = _status_text(seam)
        assert "gh-607" in out
        assert "exact_max_out" in out

    def test_it_is_not_counted_as_drift(self, seam):
        """gh-920 made this safe; a gate would say otherwise.

        The pin: `--check`'s whole output is the count, and this must not
        enter it — a CI run failing on a correct allocation is worse than the
        silence gh-921 is closing.
        """
        rc_plain, _ = _status_text(seam)
        rc_check, _ = _status_text(seam, check=True)
        assert rc_plain == 0
        assert rc_check == 0

    def test_check_does_not_print_it(self, seam):
        _, out = _status_text(seam, check=True)
        assert "NOTE" not in out
        assert "steps_u32" not in out

    def test_the_summary_still_reads_up_to_date(self, seam):
        """It is not among the conditions that take the summary out of OK."""
        _, out = _status_text(seam)
        assert "up to date" in out

    def test_json_carries_it_without_touching_the_count(self, seam):
        _, out = _status_text(seam, as_json=True)
        payload = json.loads(out)
        assert payload["inert_pass_capacity"] == [
            {
                "object": "nco",
                "method": "steps_u32",
                "c_function": "nco_steps_u32",
            }
        ]
        assert payload["drift"] == 0

    def test_a_project_out_of_the_seam_prints_no_note(self, tmp_path_factory):
        """The section is absent, not empty — an empty one is noise."""
        root = _scaffold(
            tmp_path_factory.mktemp("gh921quiet") / "p", state_only=False
        )
        _, out = _status_text(root)
        assert "NOTE" not in out
