"""gh-609: a `depends_on` inline body can express a dependency's capacity
argument declaratively instead of hand-writing a fixed arity.

Before this, an object composing another object's kernel one sample at a
time (e.g. `wfm_synth` calling `fir_execute(state->fir, &imp, 1, &sym)`)
baked the call's arity into the manifest's `impl`/`impl_file` text. When the
dependency later turned on `pass_capacity` for that method (widening the
call to its 5-arg form), the composing object's frozen `impl` text did not
follow — `jm apply` kept regenerating the stale 3/4-arg call, silently
reverting any hand patch and breaking the build.

`_object_ctx` now exposes a `{<dep>_<method>_cap}` placeholder per
`depends_on` entry crossed with each of that dependency's declared methods:
`", 1"` when `pass_capacity` is on, `""` when it is off. Since a `_step`
body only ever hands a dependency one sample, `1` is always the correct
capacity. The call site is written once, using the placeholder, and then
tracks the dependency's `pass_capacity` forever with no further edits.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import _dep_cap_placeholders, _object_ctx
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


class TestDepCapPlaceholders:
    """Pure unit tests over `_dep_cap_placeholders` — no scaffold needed."""

    def test_pass_capacity_true_yields_comma_one(self):
        cfg = {
            "fir": {"methods": [{"name": "execute", "pass_capacity": True}]}
        }
        assert _dep_cap_placeholders(cfg, ["fir"]) == {
            "fir_execute_cap": ", 1"
        }

    def test_pass_capacity_false_yields_empty_string(self):
        cfg = {
            "fir": {"methods": [{"name": "execute", "pass_capacity": False}]}
        }
        assert _dep_cap_placeholders(cfg, ["fir"]) == {"fir_execute_cap": ""}

    def test_pass_capacity_absent_defaults_to_empty_string(self):
        cfg = {"fir": {"methods": [{"name": "execute"}]}}
        assert _dep_cap_placeholders(cfg, ["fir"]) == {"fir_execute_cap": ""}

    def test_multiple_deps_and_methods(self):
        cfg = {
            "fir": {"methods": [{"name": "execute", "pass_capacity": True}]},
            "lo": {"methods": [{"name": "steps", "pass_capacity": False}]},
        }
        assert _dep_cap_placeholders(cfg, ["fir", "lo"]) == {
            "fir_execute_cap": ", 1",
            "lo_steps_cap": "",
        }

    def test_no_deps_is_empty(self):
        assert _dep_cap_placeholders({}, []) == {}

    def test_dep_with_no_methods_contributes_nothing(self):
        assert _dep_cap_placeholders({"fir": {}}, ["fir"]) == {}

    def test_object_ctx_includes_dep_placeholders(self):
        cfg = {
            "synth": {"depends_on": ["fir"]},
            "fir": {"methods": [{"name": "execute", "pass_capacity": True}]},
        }
        ctx = _object_ctx(cfg, "synth", None)
        assert ctx["fir_execute_cap"] == ", 1"
        # The usual placeholders are still present alongside the new ones.
        assert ctx["component"] == "synth"


def _scaffold(root: Path, pass_capacity: bool) -> None:
    new_run("dsp", root)
    object_run(
        root,
        "fir",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    method_run(
        root,
        "fir",
        "execute",
        None,
        "void",
        "float _Complex",
        True,  # variable_output
        [],  # multi_output
        params=[("x", "float _Complex[]")],
        pass_capacity=pass_capacity,
    )
    object_run(
        root,
        "synth",
        None,
        state_vars=[("phase", "float", "0.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    cfg = C.load(root)
    cfg["synth"]["depends_on"] = ["fir"]
    cfg["synth"]["impl"] = (
        "float complex imp = x;\n"
        "float complex sym;\n"
        "fir_execute(state->fir, &imp, 1, &sym{fir_execute_cap});\n"
        "return sym;\n"
    )
    C.save(root, cfg)
    apply_run(root)


@pytest.fixture()
def proj_with_cap(tmp_path):
    root = tmp_path / "dsp"
    _scaffold(root, pass_capacity=True)
    return root


@pytest.fixture()
def proj_without_cap(tmp_path):
    root = tmp_path / "dsp"
    _scaffold(root, pass_capacity=False)
    return root


class TestApplyExpandsDepCapPlaceholder:
    def _synth_h(self, root: Path) -> str:
        return (root / "native/inc/synth/synth_core.h").read_text(
            encoding="utf-8"
        )

    def test_pass_capacity_on_expands_to_five_arg_call(self, proj_with_cap):
        text = self._synth_h(proj_with_cap)
        assert "fir_execute(state->fir, &imp, 1, &sym, 1);" in text
        assert "{fir_execute_cap}" not in text

    def test_pass_capacity_off_expands_to_empty(self, proj_without_cap):
        text = self._synth_h(proj_without_cap)
        assert "fir_execute(state->fir, &imp, 1, &sym);" in text
        assert "{fir_execute_cap}" not in text

    def test_reapply_is_idempotent(self, proj_with_cap):
        before = self._synth_h(proj_with_cap)
        apply_run(proj_with_cap)
        after = self._synth_h(proj_with_cap)
        assert before == after
        assert after.count("fir_execute(state->fir, &imp, 1, &sym, 1);") == 1

    def test_toggling_pass_capacity_updates_call_on_next_apply(
        self, proj_with_cap
    ):
        """The whole point of gh-609: flipping the dependency's
        pass_capacity and re-applying must update the composing object's
        call site with no hand edit — no reversion, no stale arity."""
        cfg = C.load(proj_with_cap)
        execute = next(
            m for m in C.methods(cfg, "fir") if m["name"] == "execute"
        )
        execute["pass_capacity"] = False
        C.save(proj_with_cap, cfg)
        apply_run(proj_with_cap)
        text = self._synth_h(proj_with_cap)
        assert "fir_execute(state->fir, &imp, 1, &sym);" in text
        assert "fir_execute(state->fir, &imp, 1, &sym, 1);" not in text
