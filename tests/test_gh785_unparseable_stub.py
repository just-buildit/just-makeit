"""gh-785: an unparseable `.pyi` silently loses every hand-written member.

`_splice_hand_owned` finds a stub's members with `ast`, and `_member_groups`
returns `{}` rather than raising on a `SyntaxError`. So a stub that does not
parse has no members to transplant, and the fresh render replaces all of them
— `manual_stub` and `# jm:hand` alike — with no signal at all.

gh-765's guard does not catch it, and the reason is worth a test of its own:
`placeholder_regressions` filters candidates through
`(cls, name) in old_members`, and `old_members` is empty for exactly this
input. The check passes over the largest loss it could ever see.

Two things are asserted throughout rather than one. That a `# jm:hand` member
vanishes is the *unrecoverable* half — it has no manifest declaration, so
nothing can put it back — and it is the half a test written around
`manual_stub` alone would miss entirely.

The handling is deliberately asymmetric with gh-765, per the issue: a stub
that is not valid Python is itself broken, and regenerating it is the repair,
so `apply` warns and proceeds. What makes that warning act-on-able is the
`jm status` half, which fires while the members are still on disk — `apply`
does not fix this finding, it *consumes* it.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit import _stubs as S  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

#: A merge-conflict marker: the durable route to an unparseable stub. gh-625
#: closed the one jm itself opened (`def level:double(...)`), but a conflict,
#: a typo or a truncated write all land in the same state.
_CONFLICT = "<<<<<<< HEAD"

_MANUAL_BODY = '''    def execute_special(self, x: float) -> float:
        """THE AUTHOR WROTE THIS. Hand-owned via manual_stub."""
'''

_HAND_BODY = '''    # jm:hand
    def execute_ci16(self, x: int) -> int:
        """THE AUTHOR WROTE THIS TOO. Hand-owned via the jm:hand marker."""

'''


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        fn(*a, **kw)
    return buf.getvalue()


@pytest.fixture()
def project(tmp_path) -> Path:
    """A component carrying both kinds of hand-owned `.pyi` member."""
    root = tmp_path / "proj"
    _quiet(new_run, "proj", root)
    _quiet(
        object_run, root, "thing", None, state_vars=[("gain", "double", "1.0")]
    )
    # `(root, obj, name, module, arg_type, return_type, variable_output,
    # multi_output)` — a manual_stub method declares none of the shapes, so
    # every one of them is the empty/default spelling the CLI passes.
    _quiet(
        method_run,
        root,
        "thing",
        "execute_special",
        None,
        "void",
        "void",
        False,
        [],
        manual_stub=True,
    )

    pyi = root / "src" / "proj" / "thing.pyi"
    text = pyi.read_text(encoding="utf-8")
    # Fill the manual_stub placeholder the way an author would...
    start = text.index("    def execute_special(")
    end = text.index("    def ", start + 10)
    text = text[:start] + _MANUAL_BODY + "\n" + text[end:]
    # ...and add a member with no manifest declaration at all.
    text = text.replace(
        "    def destroy(self)", _HAND_BODY + "    def destroy(self)", 1
    )
    pyi.write_text(text, encoding="utf-8")
    return root


def _pyi(root: Path) -> Path:
    return root / "src" / "proj" / "thing.pyi"


def _break_it(root: Path) -> None:
    """Make the stub unparseable without removing anything from it."""
    path = _pyi(root)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "    def get_gain(self)", f"{_CONFLICT}\n    def get_gain(self)", 1
        ),
        encoding="utf-8",
    )


def _hand_owned_count(root: Path) -> int:
    return _pyi(root).read_text(encoding="utf-8").count("THE AUTHOR WROTE")


class TestTheFixtureIsHonest:
    """A test of loss is worthless if the members never survived anyway."""

    def test_both_kinds_survive_an_apply_while_the_stub_parses(self, project):
        assert _hand_owned_count(project) == 2
        _quiet(apply_run, project)
        assert _hand_owned_count(project) == 2
        text = _pyi(project).read_text(encoding="utf-8")
        assert "# jm:hand" in text
        assert S._MANUAL_STUB_PLACEHOLDER not in text


class TestTheHole:
    """The reported behaviour, and why the existing guard misses it."""

    def test_gh765s_guard_cannot_see_this_loss(self, project):
        # `placeholder_regressions` compares against `_member_groups(old)`,
        # which is empty for unparseable input — so every candidate is
        # filtered out and the check reports nothing to refuse. Stated
        # against a literal pair rather than a live render, so the assertion
        # is about the guard and not about what jm happens to emit today.
        _break_it(project)
        old = _pyi(project).read_text(encoding="utf-8")
        assert "THE AUTHOR WROTE" in old
        fresh = (
            "class Thing:\n"
            "    def execute_special(self, *a: object) -> object:\n"
            f'        """{S._MANUAL_STUB_PLACEHOLDER} hand-write this."""\n'
        )
        assert S.placeholder_regressions(old, fresh) == []
        # The same comparison over a *parseable* old stub does fire, which
        # is what makes the empty result above a hole rather than a policy.
        assert S.placeholder_regressions(
            old.replace(_CONFLICT + "\n", ""), fresh
        ) == ["Thing.execute_special"]

    def test_apply_still_discards_them(self, project):
        # Warning, not refusal: a stub that is not valid Python is broken,
        # and regenerating it is the repair. The loss is the documented
        # cost, so the test pins it rather than pretending otherwise.
        _break_it(project)
        _quiet(apply_run, project)
        assert _hand_owned_count(project) == 0

    def test_apply_says_what_it_is_discarding(self, project):
        _break_it(project)
        out = _quiet(apply_run, project)
        assert "will not survive" in out
        # Both halves named. A message listing only the manual_stub member
        # would omit the one nothing can restore.
        assert "execute_special" in out
        assert "execute_ci16" in out
        assert "thing.pyi:" in out
        assert "invalid syntax" in out


class TestStatusHoldsTheGate:
    """`apply` does not fix this finding — it consumes it."""

    def test_it_fails_the_gate_while_the_members_are_still_on_disk(
        self, project
    ):
        _break_it(project)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(project, check=True)
        text = buf.getvalue()
        assert drift >= 1, text
        assert "UNPARSEABLE (1)" in text
        assert "execute_ci16" in text
        assert "OK — up to date" not in text

    def test_the_finding_is_gone_after_the_apply_that_destroys_it(
        self, project
    ):
        # The whole reason the gate has to be on the status side: run the
        # repair and the evidence goes with the content.
        _break_it(project)
        _quiet(apply_run, project)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(project, check=True)
        assert drift == 0
        assert "UNPARSEABLE" not in buf.getvalue()
        assert _hand_owned_count(project) == 0

    def test_status_allow_does_not_suppress_it(self, project):
        # Same rule as the gh-426 dropped symbol: content loss is never
        # exempted by a path pattern.
        _break_it(project)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(project, check=True, allow=["src/proj/*.pyi"])
        assert drift >= 1, buf.getvalue()
        assert "UNPARSEABLE (1)" in buf.getvalue()

    def test_json_carries_the_same_finding(self, project):
        import json

        _break_it(project)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _status.run(project, as_json=True)
        payload = json.loads(buf.getvalue())
        assert payload["drift"] >= 1
        entry = payload["unparseable_stubs"][0]
        assert entry["path"] == "src/proj/thing.pyi"
        assert entry["hand_owned_at_risk"] == [
            "execute_ci16",
            "execute_special",
        ]


class TestItStaysQuietWhenNothingIsAtRisk:
    """A finding that fires on a harmless repair is a finding people skip."""

    def test_a_broken_stub_with_no_hand_owned_members_is_silent(
        self, tmp_path
    ):
        root = tmp_path / "plain"
        _quiet(new_run, "plain", root)
        _quiet(
            object_run,
            root,
            "thing",
            None,
            state_vars=[("gain", "double", "1.0")],
        )
        pyi = root / "src" / "plain" / "thing.pyi"
        pyi.write_text(
            pyi.read_text(encoding="utf-8").replace(
                "    def get_gain(self)",
                f"{_CONFLICT}\n    def get_gain(self)",
                1,
            ),
            encoding="utf-8",
        )
        out = _quiet(apply_run, root)
        assert "will not survive" not in out
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(root, check=True)
        assert "UNPARSEABLE" not in buf.getvalue()
        assert drift == 0

    def test_a_parseable_project_is_untouched(self, project):
        out = _quiet(apply_run, project)
        assert "will not survive" not in out
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(project, check=True)
        assert drift == 0, buf.getvalue()

    def test_an_empty_stub_is_a_first_render_not_a_failure(self):
        assert S.parse_error("") is None
        assert S.parse_error("   \n\n") is None


class TestTheTextScan:
    """`hand_owned_at_risk` reads what `ast` could not."""

    def test_an_unfilled_manual_stub_is_not_at_risk(self, project):
        # It still carries the placeholder, so there is nothing to lose —
        # counting it would inflate every message with members whose content
        # jm generates anyway.
        cfg = C.load(project)
        text = (
            "class Thing:\n"
            "    def execute_special(self, *a: Any) -> Any:\n"
            f'        """{S._MANUAL_STUB_PLACEHOLDER} hand-write this."""\n'
            "<<<<<<< HEAD\n"
        )
        assert S.hand_owned_at_risk(cfg, text) == []

    def test_a_filled_manual_stub_is_at_risk(self, project):
        cfg = C.load(project)
        text = (
            "class Thing:\n"
            "    def execute_special(self, x: float) -> float:\n"
            '        """Mine."""\n'
            "<<<<<<< HEAD\n"
        )
        assert S.hand_owned_at_risk(cfg, text) == ["execute_special"]

    def test_a_jm_hand_member_needs_no_manifest_entry(self, project):
        cfg = C.load(project)
        text = (
            "class Thing:\n"
            "    # jm:hand\n"
            "    def anything_at_all(self) -> None:\n"
            '        """Mine."""\n'
            "<<<<<<< HEAD\n"
        )
        assert S.hand_owned_at_risk(cfg, text) == ["anything_at_all"]

    def test_an_ordinary_generated_member_is_not_at_risk(self, project):
        cfg = C.load(project)
        text = (
            "class Thing:\n"
            "    def get_gain(self) -> float:\n"
            '        """Return current gain."""\n'
            "<<<<<<< HEAD\n"
        )
        assert S.hand_owned_at_risk(cfg, text) == []
