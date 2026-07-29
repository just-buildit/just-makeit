"""
gh-607: ``*_max_out()`` gains the same count parameter the binding is about
to pass to the kernel, mirroring the kernel's own parameter name per shape:

- array-arg method (``arg_type`` not ``"void"``)      -> ``n_in``
- single-array-param method (``params=[{array}]``)    -> ``<param>_len``
- generator (no arg, no params)                       -> ``n``
- all-scalar-params method (no array to size from)     -> no parameter at
  all — there is nothing to mirror, since the kernel itself takes no count.

``0`` stops meaning "unknown, allocate defensively" and becomes an ordinary
answer. Without ``pass_capacity`` the binding still clamps the allocation to
at least what the call needs (today's safety net, unchanged) — a
mechanically migrated ``return 0;`` is still safe. With ``pass_capacity``
the kernel is told its exact capacity via the 5-arg form and the clamp is
dropped, trusting the bound the kernel itself now enforces.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _scaffold(tmp_path):
    root = tmp_path / "dsp"
    new_run("dsp", root)
    object_run(
        root,
        "ddc",
        None,
        state_vars=[("g", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return root


def _core_h(root):
    return (root / "native" / "inc" / "ddc" / "ddc_core.h").read_text(
        encoding="utf-8"
    )


def _core_c(root):
    return (root / "native" / "src" / "ddc" / "ddc_core.c").read_text(
        encoding="utf-8"
    )


def _ext_c(root):
    return (root / "native" / "src" / "ddc" / "ddc_ext.c").read_text(
        encoding="utf-8"
    )


def _pyi(root):
    return (root / "src" / "dsp" / "ddc.pyi").read_text(encoding="utf-8")


class TestCountParamPerShapeInHeaderAndStub:
    def test_array_arg_shape_uses_n_in(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "execute",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        h = _core_h(root)
        assert (
            "size_t ddc_execute_max_out(ddc_state_t *state, size_t n_in);" in h
        )
        c = _core_c(root)
        assert (
            "ddc_execute_max_out(ddc_state_t *state, size_t n_in)\n{\n"
            "    (void)state; (void)n_in;" in c
        )

    def test_single_array_param_shape_uses_param_len(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "steps",
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float _Complex[]")],
        )
        h = _core_h(root)
        assert (
            "size_t ddc_steps_max_out(ddc_state_t *state, size_t x_len);" in h
        )

    def test_generator_shape_uses_n(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root, "ddc", "gen", None, "void", "float _Complex", True, []
        )
        h = _core_h(root)
        assert "size_t ddc_gen_max_out(ddc_state_t *state, size_t n);" in h

    def test_all_scalar_params_shape_stays_zero_arg(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "push",
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float")],
        )
        h = _core_h(root)
        assert "size_t ddc_push_max_out(ddc_state_t *state);" in h


class TestPythonFacingMaxOut:
    """gh-607: the Python-exposed <verb>_max_out() is a breaking API change
    — it used to take zero arguments, now it takes the mirrored count."""

    def test_array_arg_max_out_takes_n_in(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "execute",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        pyi = _pyi(root)
        assert "def execute_max_out(self, n_in: int) -> int:" in pyi
        ext = _ext_c(root)
        assert "Ddc_execute_max_out(DdcObject *self, PyObject *args)" in ext
        assert 'PyArg_ParseTuple(args, "n", &n_in)' in ext
        assert '"execute_max_out", (PyCFunction)Ddc_execute_max_out' in ext
        assert "METH_VARARGS," in ext

    def test_single_array_param_max_out_takes_param_len(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "steps",
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float _Complex[]")],
        )
        assert "def steps_max_out(self, x_len: int) -> int:" in _pyi(root)

    def test_all_scalar_params_method_exposes_no_out_or_max_out(
        self, tmp_path
    ):
        # Unaffected by gh-607: this shape was never `_enable_out`-eligible
        # (it isn't a bare-arg or single-array-param method), so it exposes
        # no Python-facing max_out() at all, before or after this change.
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "push",
            None,
            "void",
            "float _Complex",
            True,
            [],
            params=[("x", "float")],
        )
        assert '"push_max_out"' not in _ext_c(root)


class TestPassCapacityClampBehavior:
    """gh-607: without pass_capacity, max_out() is a sizing HINT and the
    binding still clamps to at least what the call needs — 0 is safe.
    With pass_capacity, the kernel is trusted with the exact bound and the
    clamp is dropped, since the kernel itself now enforces it."""

    def test_without_pass_capacity_clamp_is_kept(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "execute",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        ext = _ext_c(root)
        assert (
            "size_t _cap = ddc_execute_max_out(self->handle, (size_t)n);"
            in ext
        )
        assert "if (!_cap || _cap < _need) _cap = _need;" in ext
        assert "(void)_need;" not in ext

    def test_with_pass_capacity_clamp_is_dropped(self, tmp_path):
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "execute",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
            pass_capacity=True,
        )
        ext = _ext_c(root)
        assert (
            "size_t _cap = ddc_execute_max_out(self->handle, (size_t)n);"
            in ext
        )
        assert "if (!_cap || _cap < _need) _cap = _need;" not in ext
        assert "(void)_need;" in ext

    def test_out_validation_still_uses_max_of_max_out_and_call_size(
        self, tmp_path
    ):
        # The out= buffer-validation path is independent of pass_capacity
        # (it's about the *caller's* buffer, not the internal alloc) and
        # must keep requiring capacity for whichever is larger — gh-219
        # follow-up, unaffected by this change beyond the added argument.
        root = _scaffold(tmp_path)
        method_run(
            root,
            "ddc",
            "execute",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
            pass_capacity=True,
        )
        ext = _ext_c(root)
        assert (
            "size_t _omax = ddc_execute_max_out(self->handle, (size_t)n);"
            in ext
        )
        assert (
            "size_t _min_cap = _omax > (size_t)n ? _omax : ((size_t)n);" in ext
        )
