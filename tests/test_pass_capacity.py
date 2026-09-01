"""gh-138: `pass_capacity` on a variable_output method.

A variable_output method normally lowers to the 4-arg C form
``size_t fn(state, in, n_in, out)`` — the binding owns the buffer and sizes it
via ``fn_max_out()``. Some C APIs defensively take an explicit output capacity
as a trailing ``size_t max_out`` (e.g. to forward it to a downstream
resampler). ``pass_capacity = true`` opts into that 5-arg form: the generated
prototype, _core.c stub, and ext-binding call all carry the capacity (the
buffer-cap field jm already maintains for grow-on-demand).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._init import run as init_run
from just_makeit._method import run as method_run
from just_makeit._apply import run as apply_run


def _scaffold(root):
    new_run("dsp", root)
    init_run(root, "ddc")
    method_run(
        root,
        "ddc",
        "execute",
        None,
        "void",
        "float _Complex",
        True,  # variable_output
        [],  # multi_output
        params=[("x", "float _Complex[]")],
        pass_capacity=True,
    )


@pytest.fixture()
def proj(tmp_path):
    root = tmp_path / "dsp"
    _scaffold(root)
    return root


class TestPassCapacity:
    def test_header_decl_has_capacity(self, proj):
        h = (proj / "native/inc/ddc/ddc_core.h").read_text()
        assert (
            "size_t ddc_execute(ddc_state_t *state,"
            " const float _Complex *x, size_t x_len,"
            " float _Complex *out, size_t max_out);" in h
        )

    def test_core_c_stub_has_capacity(self, proj):
        c = (proj / "native/src/ddc/ddc_core.c").read_text()
        assert "float _Complex *out, size_t max_out)" in c
        assert "(void)max_out;" in c

    def test_ext_call_passes_capacity(self, proj):
        e = (proj / "native/src/ddc/ddc_ext.c").read_text()
        # gh-604: the capacity forwarded is the per-call allocation (_cap),
        # not a struct field — the reuse buffer and its _buf_cap are gone.
        assert "_d0, _cap)" in e
        # gh-607: pass_capacity means the kernel is told its capacity and
        # enforces the bound itself, so the alloc is the exact max_out()
        # answer — no defensive clamp, and _need is explicitly unused.
        assert (
            "size_t _cap ="
            " ddc_execute_max_out(self->handle, (size_t)PyArray_SIZE(x_arr));"
            in e
        )
        assert "if (!_cap || _cap < _need) _cap = _need;" not in e
        assert "(void)_need;" in e

    def test_config_round_trips(self, proj):
        from just_makeit._config import load, methods

        cfg = load(proj)
        execute = next(
            m for m in methods(cfg, "ddc") if m["name"] == "execute"
        )
        assert execute.get("pass_capacity") is True

    def test_apply_is_idempotent(self, proj):
        """A 5-arg header from pass_capacity matches what apply generates, so
        a second apply must not re-inject a conflicting 4-arg prototype."""
        h_before = (proj / "native/inc/ddc/ddc_core.h").read_text()
        apply_run(proj)
        h_after = (proj / "native/inc/ddc/ddc_core.h").read_text()
        assert h_before == h_after
        # exactly one declaration of ddc_execute (no duplicate)
        assert h_after.count("size_t ddc_execute(ddc_state_t *state,") == 1
