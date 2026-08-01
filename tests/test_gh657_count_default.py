"""gh-657: a void-input variable_output method can declare its count default.

``obj.ptr()`` on a snapshot/drain accessor silently changed meaning between
0.33.15 and 0.33.16 — from "return everything buffered" to "return one
sample". The signature never changed; ``count`` has defaulted to ``1`` for the
whole life of the feature. What changed is that gh-607 started feeding that
count to ``*_max_out()`` and, under ``pass_capacity``, dropped the clamp that
had been quietly rescuing it:

    0.33.15   _omax = ptr_max_out(handle);            /* natural capacity */
              _min_cap = _omax > n ? _omax : n;       /* clamp rescued it   */
    0.33.16   _omax = ptr_max_out(handle, n);         /* n is 1             */
              _min_cap = _omax;                       /* clamp gone         */

jm cannot derive the right default — the object's natural capacity lives in
the user's C — so ``count_default`` declares it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._methods import (  # noqa: E402
    _count_default_parts,
)
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _scaffold(tmp_path: Path, *, count_default: str = "") -> Path:
    root = tmp_path / "dsp"
    new_run("dsp", root)
    object_run(
        root,
        "delay",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="double _Complex",
        return_type="double _Complex",
    )
    method_run(
        root,
        "delay",
        "ptr",
        None,
        "void",
        "double _Complex",
        True,
        [],
        pass_capacity=True,
        count_default=count_default,
    )
    return root


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "delay" / "delay_ext.c").read_text(
        encoding="utf-8"
    )


def _wrapper(ext: str, fn: str) -> str:
    """Return just *fn*'s wrapper body from the generated extension source."""
    start = ext.index(f"{fn}(")
    return ext[start : ext.index("\nstatic ", start)]


def _pyi(root: Path) -> str:
    return (root / "src" / "dsp" / "delay.pyi").read_text(encoding="utf-8")


class TestCountDefaultParts:
    def test_absent_keeps_the_historical_one(self):
        assert _count_default_parts("", "delay") == ("1", "")

    def test_integer_needs_no_state_alias(self):
        init, alias = _count_default_parts("64", "delay")
        assert init == "(Py_ssize_t)(64)"
        assert alias == ""  # else -Wunused-variable on the alias

    def test_state_expression_gets_an_alias(self):
        init, alias = _count_default_parts("state->num_taps", "delay")
        assert init == "(Py_ssize_t)(state->num_taps)"
        assert alias == "    delay_state_t *state = self->handle;\n"

    def test_state_as_a_substring_does_not_trigger_the_alias(self):
        _, alias = _count_default_parts("self->handle->statemachine", "delay")
        assert alias == ""


class TestGeneratedBinding:
    def test_declared_default_seeds_the_count(self, tmp_path):
        ext = _ext(_scaffold(tmp_path, count_default="state->num_taps"))
        assert "    delay_state_t *state = self->handle;\n" in ext
        assert "Py_ssize_t n = (Py_ssize_t)(state->num_taps);" in ext

        # Ordering has to be checked inside ptr()'s own wrapper — several
        # other wrappers in the file also parse keywords.
        body = _wrapper(ext, "Delay_ptr")
        assert body.index("destroyed") < body.index(
            "delay_state_t *state = self->handle;"
        ), "state alias must follow the destroyed-handle guard"
        # A caller-supplied count still wins: the seed precedes the parse.
        assert body.index("Py_ssize_t n = (Py_ssize_t)") < body.index(
            "PyArg_ParseTupleAndKeywords"
        )

    def test_without_the_key_nothing_changes(self, tmp_path):
        ext = _ext(_scaffold(tmp_path))
        assert "Py_ssize_t n = 1;" in ext
        assert "_state_t *state = self->handle;" not in ext

    def test_stub_and_runtime_doc_do_not_leak_the_c_expression(self, tmp_path):
        root = _scaffold(tmp_path, count_default="state->num_taps")
        # A C expression is not a Python literal; both faces show `...`.
        assert "count: int = ..." in _pyi(root)
        assert "ptr(count=...)" in _ext(root)
        assert "state->num_taps" not in _pyi(root)

    def test_runtime_doc_names_the_parameter_the_kwlist_binds(self, tmp_path):
        # The doc said `n=1` while the kwlist bound `count`, which is what
        # sent the gh-657 reporter looking for a rename that never happened.
        ext = _ext(_scaffold(tmp_path))
        assert '{"count", "out", NULL}' in ext
        assert "ptr(count=1)" in ext
        assert "ptr(n=1)" not in ext


class TestManifestRoundTrip:
    def test_key_survives_save_and_reload(self, tmp_path):
        root = _scaffold(tmp_path, count_default="state->num_taps")
        toml = (root / "just-makeit.toml").read_text(encoding="utf-8")
        assert 'count_default = "state->num_taps"' in toml

        from just_makeit import _config as C

        cfg = C.load(root)
        method = next(m for m in C.methods(cfg, "delay") if m["name"] == "ptr")
        assert method["count_default"] == "state->num_taps"

    def test_script_replay_emits_the_flag(self, tmp_path, capsys):
        from just_makeit._script import run as script_run

        root = _scaffold(tmp_path, count_default="state->num_taps")
        capsys.readouterr()  # drop the scaffolder's own output
        script_run(root)
        assert '--count-default "state->num_taps"' in capsys.readouterr().out
