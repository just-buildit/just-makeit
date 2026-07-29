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

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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

    def test_out_validation_without_pass_capacity_uses_max_of_both(
        self, tmp_path
    ):
        # Without pass_capacity, the out= buffer-validation path must keep
        # requiring capacity for whichever is larger of max_out() and the
        # call's own count — gh-219 follow-up, unaffected by this change
        # beyond the added argument. The kernel is never handed `_cap`
        # here, so max_out() alone is not a trustworthy bound for a
        # generator whose steps(count) can ask for more than it advertises.
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
            "size_t _omax = ddc_execute_max_out(self->handle, (size_t)n);"
            in ext
        )
        assert (
            "size_t _min_cap = _omax > (size_t)n ? _omax : ((size_t)n);" in ext
        )

    def test_out_validation_with_pass_capacity_trusts_max_out_alone(
        self, tmp_path
    ):
        # gh-607 review (PR #617): with pass_capacity, the binding already
        # trusts the kernel with exactly _omax bytes on the internal-alloc
        # path (clamp dropped, see test_with_pass_capacity_clamp_is_dropped
        # above). Requiring a caller-supplied out= buffer to also cover
        # max(_omax, n) was a contradiction -- it rejected a buffer sized to
        # the exact bound the binding itself would have allocated. The
        # validation must now match the same trust: len(out) >= _omax only.
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
        assert "size_t _min_cap = _omax;" in ext
        assert (
            "size_t _min_cap = _omax > (size_t)n ? _omax : ((size_t)n);"
            not in ext
        )


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()

# gh-607 review (PR #617): the reviewer's own repro. A pass_capacity
# resampler at rate 0.5 has a true bound of ceil(n_in * rate) + 2 -- for
# n_in = 1024 that's 514, well under n_in itself. Before the fix, out=
# validation demanded max(514, 1024) = 1024 even though the binding itself
# was already willing to allocate and hand the kernel exactly 514
# internally. `ceil` needs <math.h>, added to the include block below.
_MAX_OUT_STUB = (
    "size_t\nrc_execute_max_out(rc_state_t *state, size_t n_in)\n{\n"
    "    (void)state; (void)n_in;\n"
    "    return 0; /* placeholder */\n}"
)
_MAX_OUT_IMPL = (
    "size_t\nrc_execute_max_out(rc_state_t *state, size_t n_in)\n{\n"
    "    return (size_t)ceil((double)n_in * state->rate) + 2;\n}"
)
_EXECUTE_STUB = (
    "size_t\nrc_execute(rc_state_t *state, const float complex *in,"
    " size_t n_in, float complex *out, size_t max_out)\n{\n"
    "    (void)state;\n    (void)in; (void)n_in;\n"
    "    (void)out; (void)max_out;\n    return 0; /* placeholder */\n}"
)
_EXECUTE_IMPL = (
    "size_t\nrc_execute(rc_state_t *state, const float complex *in,"
    " size_t n_in, float complex *out, size_t max_out)\n{\n"
    "    size_t n_out = (size_t)((double)n_in * state->rate);\n"
    "    if (n_out > max_out) n_out = max_out;\n"
    "    for (size_t i = 0; i < n_out; i++) out[i] = in[i];\n"
    "    return n_out;\n}"
)


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestPassCapacityOutBufferAcceptsExactMaxOutRuntime:
    """The reviewer's exact repro from the PR #617 review, built and run for
    real: a pass_capacity method whose true bound is smaller than n_in must
    accept an out= buffer sized to max_out()'s answer rather than requiring
    it to also cover n_in."""

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("gh607review") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(
                dest,
                "rc",
                module=None,
                state_vars=[("rate", "double", "0.5")],
                no_step=True,
            )
            method_run(
                dest,
                "rc",
                "execute",
                None,
                "float _Complex",
                "float _Complex",
                True,
                [],
                pass_capacity=True,
            )
        core = dest / "native/src/rc/rc_core.c"
        text = core.read_text(encoding="utf-8")
        assert _MAX_OUT_STUB in text, "stub shape changed; update this test"
        assert _EXECUTE_STUB in text, "stub shape changed; update this test"
        text = text.replace(_MAX_OUT_STUB, _MAX_OUT_IMPL).replace(
            _EXECUTE_STUB, _EXECUTE_IMPL
        )
        text = text.replace(
            '#include "rc/rc_core.h"',
            '#include "rc/rc_core.h"\n#include <math.h>',
        )
        core.write_text(text, encoding="utf-8")

        build = dest / "build"
        for cmd in (
            [
                "cmake",
                "-S",
                str(dest),
                "-B",
                str(build),
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            ["cmake", "--build", str(build)],
        ):
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            assert r.returncode == 0, f"{cmd[0]}:\n{r.stdout}\n{r.stderr}"
        return dest

    def _run(self, dest: Path, body: str):
        return subprocess.run(
            [sys.executable, "-c", body],
            cwd=dest,
            env={**os.environ, "PYTHONPATH": str(dest / "src")},
            capture_output=True,
            text=True,
            timeout=180,
        )

    def test_out_sized_to_max_out_is_accepted_even_though_smaller_than_n_in(
        self, built
    ):
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.rc import Rc\n"
            "r = Rc(0.5)\n"
            "n_in = 1024\n"
            "x = np.zeros(n_in, dtype=np.complex64)\n"
            "assert r.execute_max_out(n_in) == 514, r.execute_max_out(n_in)\n"
            "out = np.empty(514, dtype=np.complex64)\n"
            "result = r.execute(x, out=out)\n"
            "assert len(result) == 512, len(result)\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_out_smaller_than_max_out_is_still_rejected(self, built):
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.rc import Rc\n"
            "r = Rc(0.5)\n"
            "x = np.zeros(1024, dtype=np.complex64)\n"
            "out = np.empty(513, dtype=np.complex64)\n"
            "try:\n"
            "    r.execute(x, out=out)\n"
            "    print('accepted')\n"
            "except ValueError as e:\n"
            "    print('rejected:', e)\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip().startswith("rejected:")
        assert "need >= 514" in r.stdout
