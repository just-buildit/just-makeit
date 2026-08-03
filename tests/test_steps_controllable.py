"""Controllable state vars -> optional, keyword-capable steps() overrides.

A state var flagged ``controllable = true`` becomes an optional per-call
override on the object's ``steps()``: ``obj.steps(x, gain=2.0)`` uses the
supplied value for that block while omitting it reads the live ``self->gain``.
The override is non-persistent — it never mutates the field (gh-240, Phase B).

Covers every non-perf shape: blockwise array->array (gh-244 Phase B),
scalar->scalar (item 4 PR-1, which folds in the ``out=`` keyword unification),
and the PR-1b shapes — scalar->void sinks, void-arg generators/ticks (step()
flips METH_NOARGS->METH_VARARGS when controllable), and array-input step().
step() takes its overrides positionally (hot path); steps() takes them by
keyword. Real-scalar (float/int) fields only; complex scalars and --no-step are
rejected at generation with a clean error. Declaration is TOML-first
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

    @pytest.mark.parametrize(
        "comp,at,rt,step_sig,steps_sub",
        [
            # generator: step() positional-only, steps(n, gain=...) keyword.
            (
                "osc",
                "void",
                "float",
                "def step(self, gain: float = ..., /)",
                "def steps(self, n: int, gain: float = ...)",
            ),
            # sink: step(x, gain, /) positional-only, steps(x, gain=...) kw.
            (
                "snk",
                "float",
                "void",
                "gain: float = ..., /)",
                "def steps(self, x: NDArray[np.float32], gain: float = ...)",
            ),
        ],
    )
    def test_pr1b_module_stub(
        self, tmp_path, comp, at, rt, step_sig, steps_sub
    ):
        root = tmp_path / "proj"
        new_run("proj", root, modules=["dsp"])
        object_run(
            root,
            comp,
            "dsp",
            arg_type=at,
            return_type=rt,
            state_vars=[("gain", "float", "2.0")],
            controllable_names=frozenset({"gain"}),
        )
        stub = _stubs._obj_stub(C.load(root), comp, "proj", "dsp")
        assert step_sig in stub
        assert steps_sub in stub


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

    def test_rejects_no_step(self):
        ctx = self._ctx("float", "float")
        with pytest.raises(ValueError, match="--no-step"):
            make_step_ctx(
                ctx,
                "float",
                "float",
                no_step=True,
                controllable=[("gain", "float")],
            )

    @pytest.mark.parametrize(
        "at,rt",
        [
            ("float", "float"),  # scalar->scalar
            ("float", "void"),  # sink
            ("void", "float"),  # generator
            ("void", "void"),  # tick generator
            ("float[]", "float"),  # array-input
            ("float[]", "float[]"),  # blockwise
        ],
    )
    def test_accepts_all_pr1b_shapes(self, at, rt):
        ctx = self._ctx(at, rt)
        out = make_step_ctx(ctx, at, rt, controllable=[("gain", "float")])
        # The control param lands in the step() sig (most shapes) or, for
        # blockwise (no step()), in the steps() decl.
        assert "float gain" in (out["step_impl_def"] + out["steps_c_decl"])

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
        timeout=600,
    )
    assert cfg.returncode == 0, f"configure failed:\n{cfg.stderr}"
    bld = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
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
        timeout=600,
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
        timeout=600,
    )
    assert cfg.returncode == 0, f"configure failed:\n{cfg.stderr}"
    bld = subprocess.run(
        ["cmake", "--build", str(root / "build")],
        capture_output=True,
        text=True,
        timeout=600,
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
            timeout=600,
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
        timeout=600,
    )
    assert run.returncode == 0, f"runtime check failed:\n{run.stderr}"
    assert "SCALAR E2E OK" in run.stdout


def test_step_controllable_perf_before_after(tmp_path, capsys):
    """Measure the zero-cost-default claim in ONE interpreter (no cross-process
    noise): a controllable step()'s OMIT path should be ~indistinguishable from
    a non-controllable twin's step(), while passing the override adds only the
    extra-arg cost. Two objects in one project so both import side by side.

    The assertion is on the omit/base RATIO measured *within* a round, not on
    two independently-taken bests (gh-735). The suite runs under
    ``-n auto --dist load``, so this subprocess times itself while other xdist
    workers compile C on the same runner. Timing base to completion and only
    then timing omit means the two see different load, which is exactly how it
    failed: base held steady at 86.0/86.4 ns across two different runners while
    omit swung 233.6 -> 306.7, giving a 3.5x "regression" that does not exist
    (serially, omit is within 0.4-2.8 ns of base). Uniform slowness would have
    moved both.

    Interleaving makes the comparison contention-invariant: each round times
    all three back to back, so a load spike inflates the whole round rather
    than one term of the comparison. Taking the best ratio across rounds then
    needs only ONE round where the runner was evenly loaded -- while a genuine
    regression, being present in every round, survives the minimum.
    """
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
        "fns = (lambda: b.step(1.0),\n"
        "       lambda: c.step(1.0),\n"
        "       lambda: c.step(1.0, 3.0))\n"
        "def once(fn):\n"
        "    s = time.perf_counter()\n"
        "    for _ in range(N): fn()\n"
        "    return (time.perf_counter() - s) / N * 1e9\n"
        "best = [float('inf')] * 3\n"
        "rounds = []\n"
        # All three timed back to back, so a load spike inflates the whole
        # round rather than one term of the comparison.
        "for _ in range(7):\n"
        "    r = [once(f) for f in fns]\n"
        "    best = [min(x, y) for x, y in zip(best, r)]\n"
        "    rounds.append((r[1] / r[0], r[2] / r[0]))\n"
        # MEDIAN, not min. The minimum ratio is the round where base happened
        # to take the biggest spike, so it is biased toward passing -- it read
        # 0.73x locally, i.e. "omit is faster than base", which is not a
        # measurement anyone should gate on. The median of 7 tolerates three
        # bad rounds in either direction without inventing headroom.
        "mid = len(rounds) // 2\n"
        "omit_r = sorted(x for x, _ in rounds)[mid]\n"
        "pas_r = sorted(y for _, y in rounds)[mid]\n"
        "print(f'{best[0]:.1f} {best[1]:.1f} {best[2]:.1f} "
        "{omit_r:.3f} {pas_r:.3f}')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", measure],
        cwd=root / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stderr
    base, omit, pas, omit_ratio, pas_ratio = (
        float(v) for v in out.stdout.split()
    )
    with capsys.disabled():
        print(
            f"\n[step() perf] non-controllable step(x):    {base:6.1f} ns/call"
            f"\n[step() perf] controllable   step(x):    {omit:6.1f} ns/call "
            f"({omit - base:+.1f} vs base — omit path, median ratio "
            f"{omit_ratio:.2f}x)"
            f"\n[step() perf] controllable   step(x, g): {pas:6.1f} ns/call "
            f"({pas - base:+.1f} vs base — pass path, median ratio "
            f"{pas_ratio:.2f}x)"
        )
    # The omit path must not be pathologically slower than base -- catches e.g.
    # an accidental persistent setter call, which would cost far more than 2x.
    # Asserting the within-round median ratio rather than `omit < base * 2 + 40`
    # is what makes this survive a contended runner (gh-735): the old form
    # compared two bests taken minutes apart under different load. Serially the
    # ratio sits near 1.0, so 2.0 leaves room for noise without leaving room
    # for a regression.
    assert omit_ratio < 2.0, (base, omit, pas, omit_ratio, pas_ratio)


# ── PR-1b: generators, sinks, array-input ─────────────────────────────────────


def _make(root, comp, at, rt):
    new_run("proj", root)
    init_run(
        root,
        comp,
        state_vars=[("gain", "float", "2.0")],
        arg_type=at,
        return_type=rt,
        controllable_names=frozenset({"gain"}),
    )


class TestGeneratorFlip:
    """A controllable generator flips step() from METH_NOARGS to a
    positional-optional METH_VARARGS; a non-controllable one is unchanged."""

    def test_noncontrollable_generator_is_noargs(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root)
        init_run(root, "osc", arg_type="void", return_type="float")
        ext = (root / "native/src/osc/osc_ext.c").read_text()
        assert "Osc_step(OscObject *self, PyObject *Py_UNUSED" in ext
        assert "(PyCFunction)Osc_step,      METH_NOARGS" in ext or (
            "Osc_step," in ext and "METH_NOARGS" in ext
        )

    def test_controllable_generator_flips_to_varargs(self, tmp_path):
        root = tmp_path / "proj"
        _make(root, "osc", "void", "float")
        ext = (root / "native/src/osc/osc_ext.c").read_text()
        # step() now takes args and parses a positional-optional control.
        assert "Osc_step(OscObject *self, PyObject *args)" in ext
        assert 'PyArg_ParseTuple(args, "|f", &gain)' in ext
        assert "Osc_step," in ext and "METH_VARARGS" in ext
        # steps(n) becomes keyword-capable.
        assert '{"n", "gain", NULL}' in ext
        assert "METH_VARARGS | METH_KEYWORDS" in ext


@pytest.mark.parametrize(
    "comp,at,rt,patch_old,patch_new,checks",
    [
        (
            "osc",
            "void",
            "float",
            None,
            None,
            "o=C(gain=2.0)\n"
            "o.step(); o.step(5.0)\n"
            "try: o.step(gain=5.0); raise SystemExit('kw')\n"
            "except TypeError: pass\n"
            "assert o.steps(4).shape == (4,)\n"
            "assert o.steps(4, gain=9.0).shape == (4,)\n"
            "assert o.get_gain() == 2.0\n",
        ),
        (
            "sink",
            "float",
            "void",
            None,
            None,
            "import numpy as np\n"
            "o=C(gain=2.0)\n"
            "o.step(1.0); o.step(1.0, 5.0)\n"
            "try: o.step(1.0, gain=5.0); raise SystemExit('kw')\n"
            "except TypeError: pass\n"
            "assert o.steps(np.ones(4, dtype=np.float32)) is None\n"
            "assert o.steps(np.ones(4, dtype=np.float32), gain=9.0) is None\n"
            "assert o.get_gain() == 2.0\n",
        ),
        (
            "det",
            "float[]",
            "float",
            None,
            None,
            "import numpy as np\n"
            "o=C(gain=2.0)\n"
            "x=np.ones(4, dtype=np.float32)\n"
            "o.step(x); o.step(x, 5.0)\n"
            "try: o.step(x, gain=5.0); raise SystemExit('kw')\n"
            "except TypeError: pass\n"
            "assert o.get_gain() == 2.0\n",
        ),
    ],
)
def test_pr1b_shape_e2e(tmp_path, comp, at, rt, patch_old, patch_new, checks):
    """Build each new shape and prove step()/steps() override semantics +
    step()'s positional-only keyword rejection."""
    if _SKIP:
        pytest.skip(_SKIP)
    root = tmp_path / "proj"
    _make(root, comp, at, rt)
    _cmake_build(root)
    Klass = comp.capitalize()
    script = f"from proj import {Klass} as C\n" + checks + "print('PR1B OK')\n"
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, f"{comp} runtime failed:\n{run.stderr}"
    assert "PR1B OK" in run.stdout


# ── PR-2: controllable through the JM_DEFINE_STEPS SIMD macro (perf) ───────────


class TestPerfMacro:
    def test_macro_has_layered_ex_form(self, tmp_path):
        # A --perf project emits jm_perf.h with the EX form + the plain
        # forwarder, so a hand-written SIMD steps() can thread control params.
        root = tmp_path / "proj"
        new_run("proj", root)
        init_run(root, "c", arg_type="float", return_type="float", perf=True)
        perf_h = (root / "native/inc/jm_perf.h").read_text()
        assert "JM_DEFINE_STEPS_EX(" in perf_h
        assert "_JM_EVAL_" in perf_h
        # plain JM_DEFINE_STEPS forwards to EX with empty suffixes.
        assert (
            "JM_DEFINE_STEPS_EX(fn, state_t, sample_t, LENGTH, BATCH, CHUNK,"
            " (), ())" in perf_h
        )


def test_perf_controllable_macro_e2e(tmp_path):
    """A controllable --perf object whose steps() is hand-swapped to the SIMD
    macro threads the override through fn_steps()/fn_step()/fn_step_batch()."""
    if _SKIP:
        pytest.skip(_SKIP)
    root = tmp_path / "proj"
    new_run("proj", root)
    init_run(
        root,
        "c",
        state_vars=[
            ("gain", "float", "2.0"),
            # LENGTH=0 below never touches this, but JM_DEFINE_STEPS_EX's
            # macro body unconditionally references state->delay[...] (in a
            # loop that happens to run zero iterations whenever a SIMD tier
            # is active) — the member must still exist for the translation
            # unit to compile.
            ("delay", "float[1]", "0.0"),
        ],
        arg_type="float",
        return_type="float",
        perf=True,
        controllable_names=frozenset({"gain"}),
    )
    # step() = x * gain
    h = root / "native/inc/c/c_core.h"
    h.write_text(
        h.read_text().replace("return (float)x;", "return (float)(x * gain);")
    )
    # Replace the generated plain-loop steps() with the SIMD macro + a batch fn.
    import re

    c = root / "native/src/c/c_core.c"
    body = re.sub(r"void\s+c_steps\(.*?\n\}\n", "", c.read_text(), flags=re.S)
    body += (
        "\nstatic inline void\n"
        "c_step_batch(c_state_t *state, const float *in, float *out,"
        " float gain)\n"
        "{ (void)state; for (int i = 0; i < 4; i++) out[i] = in[i] * gain; }\n"
        "JM_DEFINE_STEPS_EX(c, c_state_t, float, 0, 4, 64,"
        " (, float gain), (, gain))\n"
    )
    c.write_text(body)
    _cmake_build(root)
    script = (
        "import numpy as np\n"
        "from proj import C\n"
        "o = C(gain=2.0)\n"
        "x = np.arange(8, dtype=np.float32)\n"
        "assert np.allclose(o.steps(x), x * 2.0), 'macro steps omit'\n"
        "assert np.allclose(o.steps(x, gain=10.0), x * 10.0), 'macro steps kw'\n"
        "assert o.step(3.0) == 6.0 and o.step(3.0, 5.0) == 15.0\n"
        "print('PERF MACRO OK')\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root / "src",
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, f"runtime failed:\n{run.stderr}"
    assert "PERF MACRO OK" in run.stdout
