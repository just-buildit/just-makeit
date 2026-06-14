"""Controllable state vars -> optional, keyword-capable steps() overrides.

A state var flagged ``controllable = true`` becomes an optional per-call
override on the object's ``steps()``: ``obj.steps(x, gain=2.0)`` uses the
supplied value for that block while omitting it reads the live ``self->gain``.
The override is non-persistent — it never mutates the field (gh-240, Phase B).

Covers the blockwise array-in / array-out shape (gh-244 Phase B) and the
scalar->scalar step()+steps() shape (item 4 PR-1, which also folds in the
``out=`` keyword unification). step() takes its overrides positionally (hot
path); steps() takes them by keyword. Real-scalar (float/int) fields only;
array-input step(), void-arg generators/sinks, and complex scalars are rejected
at generation with a clean error. Declaration is TOML-first
(``controllable_names`` threaded through generation).
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

    def test_rejects_array_input_step_shape(self):
        # array-in -> scalar-out has a step() but no steps() to attach the
        # override to; deferred past PR-1 (rejected, not a compile failure).
        ctx = self._ctx("float[]", "float")
        with pytest.raises(ValueError, match="not yet supported"):
            make_step_ctx(
                ctx, "float[]", "float", controllable=[("gain", "float")]
            )

    def test_rejects_void_arg_generator(self):
        # void-arg generators/sinks (NOARGS->VARARGS flip) are deferred.
        ctx = self._ctx("void", "float")
        with pytest.raises(ValueError, match="not yet supported"):
            make_step_ctx(
                ctx, "void", "float", controllable=[("gain", "float")]
            )

    def test_accepts_scalar_scalar(self):
        # scalar->scalar is now supported (PR-1); must NOT raise.
        ctx = self._ctx("float", "float")
        out = make_step_ctx(
            ctx, "float", "float", controllable=[("gain", "float")]
        )
        assert ", float gain)" in out["steps_c_decl"]

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


# ── Scalar -> scalar step() + steps() (item 4 PR-1) ───────────────────────────


@pytest.fixture()
def scalar(tmp_path):
    """Standalone scalar->scalar object (float -> float) with a controllable
    ``gain`` field; step() is positional, steps() keyword-capable."""
    root = tmp_path / "proj"
    new_run("proj", root)
    init_run(
        root,
        "amp",
        state_vars=[("gain", "float", "2.0")],
        arg_type="float",
        return_type="float",
        controllable_names=frozenset({"gain"}),
    )
    return root


def _cmake_build(root):
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(root / "build")],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, f"configure failed:\n{cfg.stderr}"
    bld = subprocess.run(
        ["cmake", "--build", str(root / "build")],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, f"build failed:\n{bld.stdout}\n{bld.stderr}"


class TestScalarSignature:
    def test_step_and_steps_take_control(self, scalar):
        h = (scalar / "native/inc/amp/amp_core.h").read_text()
        # step() inline gains the param; steps() decl gains it too.
        assert "float x, float gain)" in h
        assert "size_t               n, float gain);" in h

    def test_step_parses_positional_not_keyword(self, scalar):
        ext = (scalar / "native/src/amp/amp_ext.c").read_text()
        # step() stays positional (PyArg_ParseTuple), default from the field.
        assert "float gain = self->handle->gain;" in ext
        assert 'PyArg_ParseTuple(args, "f|f", &x, &gain)' in ext

    def test_steps_is_keyword_capable(self, scalar):
        ext = (scalar / "native/src/amp/amp_ext.c").read_text()
        assert '"x", "out", "gain", NULL' in ext
        assert "PyArg_ParseTupleAndKeywords" in ext

    def test_step_methoddef_stays_varargs_only(self, scalar):
        ext = (scalar / "native/src/amp/amp_ext.c").read_text()
        # step() never gets METH_KEYWORDS (per-sample hot path).
        assert "(PyCFunction)Amp_step,     METH_VARARGS," in ext
        # steps() does.
        assert (
            "(PyCFunction)(void *)Amp_steps,    METH_VARARGS | METH_KEYWORDS"
            in ext
        )

    def test_pyi_step_positional_only_steps_keyword(self, scalar):
        pyi = (scalar / "src/proj/amp.pyi").read_text()
        assert (
            "def step(self, x: float, gain: float = ..., /) -> float:" in pyi
        )
        assert (
            "out: NDArray[np.float32] | None = None, gain: float = ..." in pyi
        )


class TestOutUnification:
    """out= folded into item 4: every built-in scalar steps() is now
    keyword-capable, even with no controllable field."""

    def test_plain_scalar_steps_is_keyword_capable(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root)
        init_run(
            root, "plain", arg_type="float", return_type="float"
        )  # no controllable
        ext = (root / "native/src/plain/plain_ext.c").read_text()
        assert "PyArg_ParseTupleAndKeywords" in ext
        assert '{"x", "out", NULL}' in ext
        assert (
            "(PyCFunction)(void *)Plain_steps,    "
            "METH_VARARGS | METH_KEYWORDS" in ext
        )

    def test_plain_scalar_out_kwarg_works_e2e(self, tmp_path):
        if _SKIP:
            pytest.skip(_SKIP)
        root = tmp_path / "proj"
        new_run("proj", root)
        init_run(root, "plain", arg_type="float", return_type="float")
        _cmake_build(root)
        script = (
            "import numpy as np\n"
            "from proj import Plain\n"
            "p = Plain()\n"
            "x = np.ones(4, dtype=np.float32)\n"
            "buf = np.zeros(4, dtype=np.float32)\n"
            "r = p.steps(x, out=buf)\n"  # out= as a keyword — the fold-in
            "assert r is buf\n"
            "print('OUT KW OK')\n"
        )
        run = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root / "src",
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr
        assert "OUT KW OK" in run.stdout


def test_scalar_controllable_e2e(scalar):
    """Build a scalar object and prove step()/steps() override semantics,
    including that step() rejects a keyword call (positional-only)."""
    if _SKIP:
        pytest.skip(_SKIP)
    # y = x * gain (the override is observable).
    h = scalar / "native/inc/amp/amp_core.h"
    h.write_text(
        h.read_text().replace("return (float)x;", "return (float)(x * gain);")
    )
    _cmake_build(scalar)
    script = (
        "import numpy as np\n"
        "from proj import Amp\n"
        "a = Amp(gain=2.0)\n"
        "assert a.step(3.0) == 6.0, 'step omit reads field'\n"
        "assert a.step(3.0, 10.0) == 30.0, 'step positional override'\n"
        "try:\n"
        "    a.step(3.0, gain=10.0); raise SystemExit('kw must be rejected')\n"
        "except TypeError:\n"
        "    pass\n"
        "assert a.get_gain() == 2.0, 'override must not persist'\n"
        "x = np.arange(4, dtype=np.float32)\n"
        "assert np.allclose(a.steps(x), x * 2.0), 'steps omit'\n"
        "assert np.allclose(a.steps(x, gain=10.0), x * 10.0), 'steps kw'\n"
        "assert a.get_gain() == 2.0\n"
        "print('SCALAR E2E OK')\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=scalar / "src",
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, f"runtime check failed:\n{run.stderr}"
    assert "SCALAR E2E OK" in run.stdout


def test_step_controllable_perf_before_after(tmp_path, capsys):
    """Measure the zero-cost-default claim in ONE interpreter (no cross-process
    noise): a controllable step()'s OMIT path should be ~indistinguishable from
    a non-controllable twin's step(), while passing the override adds only the
    extra-arg cost. Two objects in one project so both import side by side.
    Reports before/after ns/call; asserts only a loose bound so CI noise can't
    flake it."""
    if _SKIP:
        pytest.skip(_SKIP)
    root = tmp_path / "proj"
    new_run("proj", root)
    # base: non-controllable. ctrl: controllable. Same shape, same package.
    init_run(
        root,
        "base",
        state_vars=[("gain", "float", "2.0")],
        arg_type="float",
        return_type="float",
    )
    init_run(
        root,
        "ctrl",
        state_vars=[("gain", "float", "2.0")],
        arg_type="float",
        return_type="float",
        controllable_names=frozenset({"gain"}),
    )
    _cmake_build(root)

    measure = (
        "import time\n"
        "from proj import Base, Ctrl\n"
        "b = Base(gain=2.0); c = Ctrl(gain=2.0)\n"
        "N = 300000\n"
        "def t(fn):\n"
        "    best = float('inf')\n"
        "    for _ in range(7):\n"  # best-of-7 rejects scheduler noise
        "        s = time.perf_counter()\n"
        "        for _ in range(N): fn()\n"
        "        best = min(best, time.perf_counter() - s)\n"
        "    return best / N * 1e9\n"
        "base = t(lambda: b.step(1.0))\n"
        "omit = t(lambda: c.step(1.0))\n"
        "pas  = t(lambda: c.step(1.0, 3.0))\n"
        "print(f'{base:.1f} {omit:.1f} {pas:.1f}')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", measure],
        cwd=root / "src",
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    base, omit, pas = (float(v) for v in out.stdout.split())
    with capsys.disabled():
        print(
            f"\n[step() perf] non-controllable step(x):    {base:6.1f} ns/call"
            f"\n[step() perf] controllable   step(x):    {omit:6.1f} ns/call "
            f"({omit - base:+.1f} vs base — omit path)"
            f"\n[step() perf] controllable   step(x, g): {pas:6.1f} ns/call "
            f"({pas - base:+.1f} vs base — pass path)"
        )
    # Loose sanity: the omit path must not be pathologically slower than base
    # (catches e.g. an accidental persistent setter call). Tolerant of noise.
    assert omit < base * 2 + 40, (base, omit, pas)
