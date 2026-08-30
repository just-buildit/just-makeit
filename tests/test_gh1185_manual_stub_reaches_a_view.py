"""gh-1185: `manual_stub` on a method a view inherits.

Declaring `manual_stub = true` on `[[<obj>.methods]]` aborted `jm apply` with a
**traceback**, whose message blamed gh-765 — "an intermittent failure of the
manual_stub transplant, not something you did … re-run the command" — for
something that reproduces every time and that no re-run can clear.

Three problems, and they are separable:

1. **The disagreement**, which is the actual bug. The stub renderer emits the
   `<<MANUAL_STUB>>` placeholder for a view as well as for the declaring
   object — a view is a second class over the same core, so it inherits the
   member. `_manual_stub_pairs`, which is what the splice reads to decide
   which members to carry across from the old stub, named the declaring
   object only. Measured on the repro: the fresh render placed placeholders at
   `[('O', 'gain2'), ('Peek', 'gain2')]` while the recogniser returned
   `{('O', 'gain2')}`. So `Peek.gain2`'s old text was not carried, the
   placeholder stood, and the gh-765 guard refused the write — **correctly**,
   about a loss the caller had just caused. The guard was right; the two sides
   disagreeing was the bug.

2. **The traceback.** `_apply.run` wrapped only the REPLAY in a handler. The
   stub write happens in the reconcile phase afterwards, so its refusals —
   carefully written for a reader — arrived at the bottom of a stack trace.

3. **The message.** It asserted the intermittent cause and gave advice that
   cannot work for the deterministic one. "Expected, just re-run" printed on a
   failure is how a papercut survives releases; this repo has the receipts.

Found by the gh-1181 sweep rather than by a user, and worth recording why that
matters: the sweep's own assertion never ran, because `apply` never completed.
An `apply` that aborts is not a passing case, it is an unmeasured one.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _stubs as S  # noqa: E402


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


def _declare(root: Path, anchor: str, line: str) -> None:
    p = root / "objects" / "o.toml"
    body = p.read_text(encoding="utf-8")
    assert body.count(anchor) == 1, body
    p.write_text(body.replace(anchor, anchor + line + "\n", 1), "utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The issue's repro, exactly: an object with a method, a view over it,
    applied and clean before the manifest is touched."""
    assert _cli("new", "sw", cwd=tmp_path).returncode == 0
    root = tmp_path / "sw"
    for step in (
        ("module", "m"),
        ("object", "o", "--module", "m", "--state", "g:double:1.0"),
        (
            "method",
            "o",
            "gain2",
            "--module",
            "m",
            "--arg-type",
            "double",
            "--return-type",
            "double",
        ),
        ("view", "o", "Peek", "--module", "m", "--create-fn", "o_create_peek"),
    ):
        out = _cli(*step, cwd=root)
        assert out.returncode == 0, f"{step}: {out.stdout}{out.stderr}"
    assert _cli("apply", cwd=root).returncode == 0
    assert _cli("status", "--check", cwd=root).returncode == 0
    return root


class TestTheRepro:
    def test_apply_completes(self, project: Path) -> None:
        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        out = _cli("apply", cwd=project)
        assert out.returncode == 0, out.stdout + out.stderr

    def test_no_traceback_anywhere(self, project: Path) -> None:
        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        out = _cli("apply", cwd=project)
        assert "Traceback" not in out.stderr, out.stderr

    def test_both_classes_keep_their_stub_text(self, project: Path) -> None:
        """What `manual_stub` means: the member is hand-owned, so the text
        already in the stub survives. It has to survive for the view too, or
        the placeholder replaces real content — which is what the guard was
        refusing."""
        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        assert _cli("apply", cwd=project).returncode == 0
        pyi = (project / "src/sw/m/m.pyi").read_text(encoding="utf-8")
        assert pyi.count("def gain2(self, x: float) -> float:") == 2, pyi
        assert "<<MANUAL_STUB>>" not in pyi, pyi

    def test_status_is_clean_and_apply_is_idempotent(
        self, project: Path
    ) -> None:
        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        assert _cli("apply", cwd=project).returncode == 0
        assert _cli("status", "--check", cwd=project).returncode == 0
        again = _cli("apply", cwd=project)
        assert again.returncode == 0
        assert "already matches" in again.stdout, again.stdout


class TestWhichClassesInheritIt:
    """`_manual_stub_pairs` is the recogniser the splice reads. It has to
    agree with the renderer about which classes carry the member, so these
    assert the set directly — the end-to-end tests above can only see the
    cases that reach a refusal."""

    @staticmethod
    def _pairs(**view) -> set:
        cfg = {
            "project": {"name": "p"},
            "o": {
                "methods": [
                    {"name": "gain2", "manual_stub": True},
                    {"name": "plain"},
                ],
                "views": [{"class_name": "Peek", **view}],
            },
        }
        return S._manual_stub_pairs(cfg)

    def test_a_view_inherits_it(self) -> None:
        assert self._pairs() == {("O", "gain2"), ("Peek", "gain2")}

    def test_a_method_without_the_key_is_not_hand_owned(self) -> None:
        assert ("O", "plain") not in self._pairs()
        assert ("Peek", "plain") not in self._pairs()

    def test_an_excluded_method_is_not_the_views(self) -> None:
        """`exclude_methods` means the view does not have it, so there is
        nothing for the renderer to place a placeholder in."""
        got = self._pairs(exclude_methods=["gain2"])
        assert got == {("O", "gain2")}

    def test_a_views_own_override_decides(self) -> None:
        """gh-1011: a view may override a parent method. Then the view's own
        entry answers the question for the view — and if it does not carry
        the key, the view's member is not hand-owned."""
        got = self._pairs(methods=[{"name": "gain2"}])
        assert got == {("O", "gain2")}

    def test_a_view_can_declare_its_own(self) -> None:
        got = self._pairs(methods=[{"name": "solo", "manual_stub": True}])
        assert ("Peek", "solo") in got

    def test_a_view_with_no_class_name_is_skipped(self) -> None:
        cfg = {
            "project": {"name": "p"},
            "o": {
                "methods": [{"name": "gain2", "manual_stub": True}],
                "views": [{}],
            },
        }
        assert S._manual_stub_pairs(cfg) == {("O", "gain2")}


class TestTheRefusalIsADiagnostic:
    """Problem 2, on its own. The reconcile phase can still refuse for other
    reasons (gh-1092, gh-765), and none of them should arrive as a stack
    trace: they are questions about a manifest the author can edit.

    Driven by blinding the recogniser, which reproduces the ORIGINAL failure
    exactly rather than inventing a new one — the same ValueError, from the
    same call, in the same phase.
    """

    def test_it_exits_one_with_an_error_line(
        self, project: Path, monkeypatch
    ) -> None:
        import contextlib

        from just_makeit import _apply

        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        monkeypatch.setattr(S, "_manual_stub_pairs", lambda cfg: set())
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(err):
                with pytest.raises(SystemExit) as exc:
                    _apply.run(project)
        assert exc.value.code == 1
        assert err.getvalue().startswith("error: refusing to write a stub")

    def test_nothing_is_written(self, project: Path, monkeypatch) -> None:
        """The message says so, so it has to be true."""
        import contextlib

        from just_makeit import _apply

        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        pyi = project / "src/sw/m/m.pyi"
        before = pyi.read_bytes()
        monkeypatch.setattr(S, "_manual_stub_pairs", lambda cfg: set())
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                with pytest.raises(SystemExit):
                    _apply.run(project)
        assert pyi.read_bytes() == before


class TestTheMessageDoesNotPromiseARerun:
    """Problem 3. The old text asserted the intermittent cause and told the
    reader to re-run — advice that cannot clear the deterministic one, given
    about the case that actually reached users."""

    @staticmethod
    def _message(project: Path, monkeypatch) -> str:
        """The real message, from the real call.

        A hand-built old/new pair does not reach the guard — the splice has to
        recognise member GROUPS for a placeholder to regress — so building one
        here would be a fixture of what I think the parser accepts, testing
        nothing about the text a user sees.
        """
        import contextlib

        from just_makeit import _apply

        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        monkeypatch.setattr(S, "_manual_stub_pairs", lambda cfg: set())
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(err):
                with pytest.raises(SystemExit):
                    _apply.run(project)
        return err.getvalue()

    def test_it_does_not_claim_the_failure_is_intermittent(
        self, project: Path, monkeypatch
    ) -> None:
        msg = self._message(project, monkeypatch)
        assert "an intermittent failure of the manual_stub" not in msg, msg

    def test_it_names_both_causes(self, project: Path, monkeypatch) -> None:
        msg = self._message(project, monkeypatch)
        assert "gh-1185" in msg, msg
        assert "gh-765" in msg, msg

    def test_it_says_how_to_tell_them_apart(
        self, project: Path, monkeypatch
    ) -> None:
        """Without this the reader has two causes and no way to choose."""
        msg = self._message(project, monkeypatch)
        assert "same members again" in msg, msg

    def test_it_still_says_nothing_was_written(
        self, project: Path, monkeypatch
    ) -> None:
        assert "Nothing has been written" in self._message(
            project, monkeypatch
        )


class TestTheCfgShapeIsReal:
    """The unit tests above hand-build a cfg, which is a fixture of what I
    think a manifest looks like. This ties it back to one the tool wrote."""

    def test_a_real_manifest_produces_the_same_shape(
        self, project: Path
    ) -> None:
        _declare(project, 'name = "gain2"\n', "manual_stub = true")
        cfg = C.load(project)
        assert S._manual_stub_pairs(cfg) == {("O", "gain2"), ("Peek", "gain2")}
