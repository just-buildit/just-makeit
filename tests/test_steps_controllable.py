"""Controllable state vars -> optional, keyword-capable steps() overrides.

A state var flagged ``controllable = true`` becomes an optional per-call
override on the object's ``steps()``: ``obj.steps(x, gain=2.0)`` uses the
supplied value for that block while omitting it reads the live ``self->gain``.
The override is non-persistent — it never mutates the field (gh-240, Phase B).

Scope (PR-1): the blockwise array-in / array-out steps() shape with
real-scalar (float/int) fields only.  Declaration is TOML-first
(``controllable_names`` threaded through generation); other shapes and
complex scalars are rejected at generation with a clean error.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._context import (
    make_perf_ctx,
    make_sample_ctx,
    make_step_ctx,
)
from just_makeit import _stubs
from just_makeit._init import run as init_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


@pytest.fixture()
def amp(tmp_path):
    """Standalone blockwise object (float[] -> float[]) with a controllable
    ``gain`` field defaulting to 2.0."""
    root = tmp_path / "proj"
    new_run("proj", root)
    init_run(
        root,
        "amp",
        state_vars=[("gain", "float", "2.0")],
        arg_type="float[]",
        return_type="float[]",
        controllable_names=frozenset({"gain"}),
    )
    return root


# ── Sacred C signature (the declared, intentional change) ─────────────────────


class TestCoreSignature:
    def test_header_steps_takes_control_param(self, amp):
        h = (amp / "native/inc/amp/amp_core.h").read_text()
        assert "float          *out, float gain);" in h

    def test_impl_steps_takes_control_param(self, amp):
        c = (amp / "native/src/amp/amp_core.c").read_text()
        assert "float          *out, float gain)" in c


# ── Binding (keyword-capable; default sourced from the live field) ────────────


class TestExtBinding:
    def test_kwlist_includes_control(self, amp):
        ext = (amp / "native/src/amp/amp_ext.c").read_text()
        assert '"x", "out", "gain", NULL' in ext

    def test_default_reads_live_state_field(self, amp):
        ext = (amp / "native/src/amp/amp_ext.c").read_text()
        assert "float gain = self->handle->gain;" in ext

    def test_parses_with_keywords(self, amp):
        ext = (amp / "native/src/amp/amp_ext.c").read_text()
        assert "PyArg_ParseTupleAndKeywords(args, kwds," in ext
        assert '"O|Of", kwlist,' in ext
        assert "&x_obj, &out_obj, &gain" in ext

    def test_both_call_sites_pass_control(self, amp):
        ext = (amp / "native/src/amp/amp_ext.c").read_text()
        # out= path and the freshly-allocated path both thread `gain` through.
        assert ext.count("PyArray_DATA(out_arr), gain)") == 1
        assert "(PyArrayObject *)out), gain)" in ext

    def test_methoddef_is_keyword_capable(self, amp):
        ext = (amp / "native/src/amp/amp_ext.c").read_text()
        assert (
            '{"steps",    (PyCFunction)(void *)Amp_steps,'
            "    METH_VARARGS | METH_KEYWORDS," in ext
        )


# ── Type stub ─────────────────────────────────────────────────────────────────


class TestStub:
    def test_pyi_exposes_optional_kwarg(self, amp):
        pyi = (amp / "src/proj/amp.pyi").read_text()
        assert "gain: float = ..." in pyi


class TestModuleStub:
    """The module ``.pyi`` (``_stubs._obj_stub``) must render the blockwise
    shape as ``steps()`` (not the old, wrong ``step()``) and carry the
    controllable kwarg."""

    @pytest.fixture()
    def mod(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, modules=["dsp"])
        object_run(
            root,
            "fir",
            "dsp",
            arg_type="float[]",
            return_type="float[]",
            state_vars=[("g", "float", "1.0")],
        )
        object_run(
            root,
            "biq",
            "dsp",
            arg_type="float[]",
            return_type="float[]",
            state_vars=[("gain", "float", "2.0")],
            controllable_names=frozenset({"gain"}),
        )
        return root

    def test_plain_blockwise_renders_steps_not_step(self, mod):
        cfg = C.load(mod)
        stub = _stubs._obj_stub(cfg, "fir", "proj", "dsp")
        assert "def steps(" in stub
        assert "def step(" not in stub

    def test_controllable_kwarg_in_module_stub(self, mod):
        cfg = C.load(mod)
        stub = _stubs._obj_stub(cfg, "biq", "proj", "dsp")
        assert "gain: float = ..." in stub
        assert "def step(" not in stub


# ── Manifest round-trip ───────────────────────────────────────────────────────


class TestConfig:
    def test_serialized_flag(self, amp):
        toml = (amp / "just-makeit.toml").read_text()
        assert "controllable = true" in toml

    def test_accessor_roundtrip(self, amp):
        cfg = C.load(amp)
        assert C.controllable_state_vars(cfg, "amp") == [("gain", "float")]
        assert C.controllable_names(cfg, "amp") == frozenset({"gain"})


# ── Generation-time validation (clean errors, not compile failures) ───────────


class TestValidation:
    def _ctx(self, arg_type, return_type):
        ctx = {"component": "c", "Component": "C"}
        ctx.update(make_sample_ctx(arg_type, return_type))
        ctx.update(make_perf_ctx(False))
        return ctx

    def test_rejects_non_blockwise_shape(self):
        ctx = self._ctx("float", "float")
        with pytest.raises(ValueError, match="blockwise"):
            make_step_ctx(
                ctx, "float", "float", controllable=[("gain", "float")]
            )

    def test_rejects_complex_scalar(self):
        ctx = self._ctx("float _Complex[]", "float _Complex[]")
        with pytest.raises(ValueError, match="real scalar"):
            make_step_ctx(
                ctx,
                "float _Complex[]",
                "float _Complex[]",
                controllable=[("g", "float _Complex")],
            )


# ── End-to-end build + override semantics ─────────────────────────────────────


def test_controllable_override_e2e(amp):
    """Build the scaffold and prove: omitting reads the live field, passing
    overrides it for that block, and the override never mutates state."""
    if _SKIP:
        pytest.skip(_SKIP)

    # Replace the pass-through body with an observable `out = in * gain`.
    core_c = amp / "native/src/amp/amp_core.c"
    body = core_c.read_text().replace(
        "out[i] = (float)in[i];", "out[i] = in[i] * gain;"
    )
    core_c.write_text(body)

    build = amp / "build"
    cfg = subprocess.run(
        ["cmake", "-S", str(amp), "-B", str(build)],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, f"configure failed:\n{cfg.stderr}"
    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, f"build failed:\n{bld.stdout}\n{bld.stderr}"

    script = (
        "import numpy as np\n"
        "from proj import Amp\n"
        "a = Amp(gain=2.0)\n"
        "x = np.ones(4, dtype=np.float32)\n"
        "assert np.allclose(a.steps(x), 2.0), 'default should read field'\n"
        "assert np.allclose(a.steps(x, gain=10.0), 10.0), 'kw override'\n"
        "assert np.allclose(a.steps(x, None, 5.0), 5.0), 'positional override'\n"
        "assert a.get_gain() == 2.0, 'override must not persist'\n"
        "out = np.zeros(4, dtype=np.float32)\n"
        "r = a.steps(x, out, gain=3.0)\n"
        "assert r is out and np.allclose(out, 3.0), 'out= + override compose'\n"
        "print('E2E OK')\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=amp / "src",
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, f"runtime check failed:\n{run.stderr}"
    assert "E2E OK" in run.stdout
