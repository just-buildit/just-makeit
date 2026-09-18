"""A composer source's bridge can say WHY it refused (gh-1307).

`[module.X.source.generates] bridge_fn` builds the composed generator lazily,
and every NULL it returned surfaced as one fixed
``RuntimeError("<bridge_fn> returned NULL")`` -- although a bridge refuses for
configuration reasons (a source with no payload, a frame the type cannot
carry, a sweep with no span) far more often than for memory. doppler's users
hit it by writing ``chirp(f_end=2e5).steps(n)``.

``bridge_error_fn`` is the optional companion: ``const char *fn(const
<struct> *, double fs)``, asked only after a refusal, with the bridge's own
arguments. A sentence is raised as ``ValueError`` (gh-482's category for a
refused ``create()``); NULL keeps the ``RuntimeError``, so an allocation
failure still reads as one.

These pin the generated text. The behaviour -- a real bridge refusing, a real
``ValueError`` with the project's sentence -- is proven compiled, by the
``composer_seams`` example (``make test-examples``), whose demo asserts it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from test_composer_codegen import _cfg  # noqa: E402

from just_makeit import _composer  # noqa: E402


def _with_generates(extra: dict) -> dict:
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["source"]["generates"] = {
        "generator": "wfm_synth",
        "bridge_fn": "wfm_source_to_synth",
        **extra,
    }
    return cfg


def _ext(cfg: dict) -> str:
    return _composer.render_ext(cfg, "wfm_compose")


class TestTheBindingAsksWhy:
    def test_a_declared_reason_is_raised_as_value_error(self):
        ext = _ext(_with_generates({"bridge_error_fn": "wfm_why_not"}))
        assert "const char *why = wfm_why_not(&self->src, self->fs);" in ext
        assert "PyErr_SetString(PyExc_ValueError, why);" in ext

    def test_no_reason_still_says_the_bridge_returned_null(self):
        """NULL from the error fn is "no reason to give", not a success."""
        ext = _ext(_with_generates({"bridge_error_fn": "wfm_why_not"}))
        assert '"wfm_source_to_synth returned NULL"' in ext

    def test_the_question_is_asked_only_after_a_refusal(self):
        ext = _ext(_with_generates({"bridge_error_fn": "wfm_why_not"}))
        refused = ext.index("if (!self->_gen) {\n            const char *why")
        built = ext.index("self->_gen = wfm_source_to_synth(")
        assert built < refused

    def test_undeclared_changes_nothing(self):
        """No key, no new behaviour: the old RuntimeError, verbatim."""
        ext = _ext(_with_generates({}))
        assert "PyExc_ValueError, why" not in ext
        assert "wfm_why_not" not in ext
        assert (
            "PyErr_SetString(PyExc_RuntimeError,\n"
            '                            "wfm_source_to_synth returned NULL");'
        ) in ext


class TestTheSignatureIsPublished:
    """gh-998: jm owns the prototype, so the bridge header carries it."""

    def test_declared_in_the_bridge_header(self):
        h = _composer.render_bridge_h(
            _with_generates({"bridge_error_fn": "wfm_why_not"}), "wfm_compose"
        )
        struct = _cfg()["module"]["wfm_compose"]["source"]["struct"]
        assert f"const char *wfm_why_not(const {struct} *, double);" in h

    def test_absent_when_undeclared(self):
        h = _composer.render_bridge_h(_with_generates({}), "wfm_compose")
        assert "const char *" not in h
