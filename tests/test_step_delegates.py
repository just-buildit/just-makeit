"""gh-208: `--step-delegates-to-steps` generates step() as a thin delegator
to steps(), so the per-sample algorithm exists once and step() == steps(.., 1)
byte-for-byte under -ffast-math (no separate inlined scalar body to contract
into FMAs differently).
"""

import contextlib
import io
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._config import load, save, step_delegates
from just_makeit._script import run as script_run


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _scaffold(tmp_path, arg_type, return_type, *, delegate=True):
    root = tmp_path / "p"
    _silent(new_run, "p", root)
    _silent(
        object_run,
        root,
        "qpsk",
        module=None,
        state_vars=[("c", "float", "0.5f")],
        arg_type=arg_type,
        return_type=return_type,
        step_delegates=delegate,
    )
    h = (root / "native/inc/qpsk/qpsk_core.h").read_text(encoding="utf-8")
    c = (root / "native/src/qpsk/qpsk_core.c").read_text(encoding="utf-8")
    return root, h, c


def _code_lines(block: str) -> str:
    """Drop comment-only lines so a recursion check ignores the prose that
    mentions step() in the steps() body."""
    out = []
    for ln in block.splitlines():
        s = ln.strip()
        if s.startswith(("/*", "*", "//")):
            continue
        out.append(ln)
    return "\n".join(out)


class TestDelegatorBodies:
    def test_scalar_transform(self, tmp_path):
        _, h, c = _scaffold(tmp_path, "float _Complex", "float _Complex")
        # step() forwards to steps(.., 1)
        assert "qpsk_steps(state, &x, &y, 1);" in h
        # state is no longer const (steps() mutates it)
        assert "const qpsk_state_t *state, float complex x" not in h
        # a forward decl precedes the inline step() so it compiles
        assert "Forward decl" in h
        # steps() body must NOT call step() (would recurse)
        steps_body = c[c.find("void qpsk_steps(") :]
        assert "qpsk_step(state" not in _code_lines(steps_body)

    def test_scalar_sink(self, tmp_path):
        _, h, c = _scaffold(tmp_path, "float _Complex", "void")
        assert "qpsk_steps(state, &x, 1);" in h
        steps_body = c[c.find("void qpsk_steps(") :]
        assert "qpsk_step(state" not in _code_lines(steps_body)

    def test_void_arg_generator(self, tmp_path):
        _, h, c = _scaffold(tmp_path, "void", "float _Complex")
        assert "qpsk_steps(state, &y, 1);" in h
        steps_body = c[c.find("void qpsk_steps(") :]
        assert "qpsk_step(state" not in _code_lines(steps_body)

    def test_void_arg_ticker(self, tmp_path):
        _, h, c = _scaffold(tmp_path, "void", "void")
        assert "qpsk_steps(state, 1);" in h
        steps_body = c[c.find("void qpsk_steps(") :]
        assert "qpsk_step(state" not in _code_lines(steps_body)

    def test_non_delegate_unchanged(self, tmp_path):
        """Without the flag, step() keeps its inlined body and steps() loops
        calling step() — the established behavior."""
        _, h, c = _scaffold(
            tmp_path, "float _Complex", "float _Complex", delegate=False
        )
        assert "qpsk_steps(state, &x, &y, 1);" not in h
        assert "return (float complex)x;" in h
        assert "output[i] = qpsk_step(state, input[i]);" in c


class TestConfigRoundTrip:
    def test_flag_persists(self, tmp_path):
        root, _, _ = _scaffold(tmp_path, "float _Complex", "float _Complex")
        assert step_delegates(load(root), "qpsk") is True
        # survives a save/load cycle
        _silent(save, root, load(root))
        assert step_delegates(load(root), "qpsk") is True

    def test_script_emits_flag(self, tmp_path):
        root, _, _ = _scaffold(tmp_path, "float _Complex", "float _Complex")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            script_run(root)
        assert "--step-delegates-to-steps" in out.getvalue()

    def test_absent_when_off(self, tmp_path):
        root, _, _ = _scaffold(
            tmp_path, "float _Complex", "float _Complex", delegate=False
        )
        assert step_delegates(load(root), "qpsk") is False
        man = (root / "just-makeit.toml").read_text(encoding="utf-8")
        assert "step_delegates_to_steps" not in man


class TestCliValidation:
    """The flag only makes sense for a per-sample scalar step()."""

    def _run(self, args):
        from just_makeit import _cli_object

        _cli_object.run(args)

    def test_rejected_with_array_arg(self):
        with pytest.raises(SystemExit):
            self._run(
                ["fir", "--arg-type", "float[]", "--step-delegates-to-steps"]
            )

    def test_rejected_with_no_step(self):
        with pytest.raises(SystemExit):
            self._run(["fir", "--no-step", "--step-delegates-to-steps"])

    def test_rejected_with_variable_output(self):
        with pytest.raises(SystemExit):
            self._run(
                ["fir", "--variable-output", "--step-delegates-to-steps"]
            )

    def test_forwarded_to_object_run(self):
        with patch("just_makeit._object.run") as mock_run:
            self._run(["fir", "--step-delegates-to-steps"])
        assert mock_run.call_args.kwargs["step_delegates"] is True
