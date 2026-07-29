"""gh-600: multi-output variable_output overflowed a fixed-cap heap buffer.

Two independent defects in the same generated block, both invisible to a
project that hand-wrote its fragment, both surfacing the moment doppler let
`jm apply` recreate it.

**Bug 1 — the signature did not match the body.** The wrapper was emitted as
``(<C>Object *self, PyObject *args)`` unconditionally, while the body parsed
with ``PyArg_ParseTupleAndKeywords(args, kwds, ...)`` whenever the method had
params, and the ``PyMethodDef`` row already said
``METH_VARARGS | METH_KEYWORDS``. Result: ``'kwds' undeclared``. Loud, and a
one-line arity fix — the single-output sibling in the same file was correct.

**Bug 2 — the output buffers never grew.** The buffers were malloc'd once in
``__init__`` at ``max_out()``, and the method then wrote ``n`` elements into
them with **no capacity check and no grow-on-demand**; ``_buf_cap`` was stored
and never read again. ``steps(n)`` for any ``n`` past that cap corrupted the
heap — reproduced here as a hard crash of a child interpreter.

doppler had already hit and fixed exactly this by hand (doppler#116); the
hand fragment carried a comment saying so. The generator's *single*-output
path had learned the lesson (grow-on-demand + the gh-219 retired freelist +
the gh-437 live-view check) and its multi-output path had not — so adopting
the declarative form silently reintroduced memory corruption.

**The fix** makes NumPy own every output: each call allocates its arrays at
``max(max_out(), n)``, the kernel writes straight into them, and a trimmed
view of the filled prefix is returned pinned to the full array. That deletes
the instance buffer, the ``__init__`` malloc, the retired freelist and the
view-liveness weakref for this shape — the aliasing hazards those exist to
manage cannot arise when no memory is shared between calls. It is also what
doppler's hand fragment already did.

``TestRuntime`` is the test that would have caught this: the defect is
invisible in generated *text* (the old code looked reasonable) and only shows
up when a compiled extension is called with a large enough ``n``.
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

from just_makeit._context._methods import make_methods_ctx
from just_makeit._method import _methods_c_stub_variable
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

MULTI = {
    "name": "steps_ovf",
    "arg_type": "void",
    "return_type": "uint32_t",
    "variable_output": True,
    "multi_output": ["uint8_t"],
    "max_out": 65536,
}
MULTI_CTRL = dict(
    MULTI,
    name="steps_ovf_ctrl",
    params=[{"name": "ctrl", "type": "float _Complex[]"}],
)


def _c(method: dict) -> str:
    return make_methods_ctx("nco", "Nco", [method])["extra_methods_c"]


class TestSignatureMatchesBody:
    """Bug 1: kwds in the body demands kwds in the signature."""

    def test_params_shape_takes_kwds(self):
        src = _c(MULTI_CTRL)
        assert (
            "Nco_steps_ovf_ctrl(NcoObject *self, PyObject *args,"
            " PyObject *kwds)" in src
        )
        assert "PyArg_ParseTupleAndKeywords(args, kwds," in src

    def test_no_params_shape_stays_positional(self):
        src = _c(MULTI)
        assert "Nco_steps_ovf(NcoObject *self, PyObject *args)" in src
        assert "kwds" not in src

    def test_keyword_methoddef_casts_through_void(self):
        # A METH_KEYWORDS wrapper is a PyCFunctionWithKeywords; casting it
        # straight to PyCFunction is an incompatible function-pointer cast.
        pmd = make_methods_ctx("nco", "Nco", [MULTI_CTRL])[
            "extra_methods_pymethoddef"
        ]
        assert "(PyCFunction)(void *)Nco_steps_ovf_ctrl" in pmd
        assert "METH_VARARGS | METH_KEYWORDS" in pmd


class TestNoSharedBuffer:
    """Bug 2: no instance buffer means nothing to overflow."""

    def test_no_buffer_fields_declared(self):
        ctx = make_methods_ctx("nco", "Nco", [MULTI])
        assert "_steps_ovf_buf" not in ctx["extra_buf_fields"]
        assert ctx["extra_buf_alloc"] == ""
        assert "_steps_ovf_buf" not in ctx["extra_buf_free"]

    def test_capacity_is_decided_per_call(self):
        src = _c(MULTI)
        assert "size_t _need = (size_t)n;" in src
        assert "size_t _cap = nco_steps_ovf_max_out(self->handle);" in src
        # The whole point: cap must cover the caller's n, not just max_out().
        assert "if (!_cap || _cap < _need) _cap = _need;" in src

    def test_outputs_are_numpy_owned(self):
        src = _c(MULTI)
        assert "PyArray_SimpleNew(1, &_adim, NPY_UINT32)" in src
        assert "PyArray_SimpleNew(1, &_adim, NPY_UINT8)" in src
        assert "PyArray_DATA((PyArrayObject *)arr0)" in src
        assert "PyArray_DATA((PyArrayObject *)arr1)" in src

    def test_result_is_trimmed_to_n_out(self):
        src = _c(MULTI)
        assert "npy_intp _odim = (npy_intp)n_out;" in src
        # The view is pinned to the full allocation, which owns the memory.
        assert "PyArray_SetBaseObject((PyArrayObject *)v0, arr0);" in src
        assert "PyArray_SetBaseObject((PyArrayObject *)v1, arr1);" in src

    def test_single_output_keeps_its_reuse_buffer(self):
        """The fix must not disturb the single-output hot path."""
        single = dict(MULTI)
        del single["multi_output"]
        ctx = make_methods_ctx("nco", "Nco", [single])
        assert "_steps_ovf_buf" in ctx["extra_buf_fields"]
        assert "_steps_ovf_retired" in ctx["extra_buf_fields"]
        assert "_steps_ovf_view_ref" in ctx["extra_buf_fields"]
        assert "malloc" in ctx["extra_buf_alloc"]

    def test_input_array_released_on_the_allocation_failure_path(self):
        src = _c(MULTI_CTRL)
        fail = src.split("if (!arr0 || !arr1) {")[1].split("}")[0]
        assert "Py_DECREF(ctrl_arr);" in fail


class TestStubSuppressesEveryOutput:
    def test_extra_output_is_voided(self):
        stub = _methods_c_stub_variable(
            "nco", "steps_ovf", "void", "uint32_t", ["uint8_t"]
        )
        # Without (void)out1 the fresh scaffold warns before any user code.
        assert "(void)out; (void)out1;" in stub


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

KERNEL = """    (void)state;
    for (size_t i = 0; i < n; i++) { out[i] = (uint32_t)i; out1[i] = (uint8_t)(i & 1); }
    return n;"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestRuntime:
    """The only test that could have caught bug 2 — it needs a real call."""

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("gh600") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(dest, "nco", module=None, arg_type="void", no_step=True)
            method_run(
                dest,
                "nco",
                "steps_ovf",
                None,
                "void",
                "uint32_t",
                True,
                ["uint8_t"],
                max_out=65536,
            )
        core = dest / "native/src/nco/nco_core.c"
        patched = core.read_text("utf-8").replace(
            "    (void)state;\n    (void)n;\n    (void)out; (void)out1;\n"
            "    return 0; /* placeholder */",
            KERNEL,
        )
        assert KERNEL in patched, "stub shape changed; update this test"
        core.write_text(patched, encoding="utf-8")

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

    def test_n_far_past_max_out_does_not_corrupt_the_heap(self, built):
        """max_out() is 65536; this asked for 393216 and crashed the process."""
        r = self._run(
            built,
            "from p.nco import Nco\n"
            "a, b = Nco().steps_ovf(393216)\n"
            "assert len(a) == 393216 and len(b) == 393216, (len(a), len(b))\n"
            "assert a[-1] == 393215 and b[-1] == 1\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, (
            f"crashed with {r.returncode} (was a segfault/abort pre-fix)\n"
            f"{r.stdout}\n{r.stderr}"
        )
        assert r.stdout.strip() == "ok"

    def test_lengths_are_exact_across_the_cap_boundary(self, built):
        r = self._run(
            built,
            "from p.nco import Nco\n"
            "o = Nco()\n"
            "for n in (1, 65535, 65536, 65537, 200000):\n"
            "    a, b = o.steps_ovf(n)\n"
            "    assert len(a) == n and len(b) == n, (n, len(a), len(b))\n"
            "    assert a[-1] == n - 1\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_a_retained_result_is_not_overwritten_by_later_calls(self, built):
        """The gh-437 hazard cannot arise when no memory is shared."""
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.nco import Nco\n"
            "o = Nco()\n"
            "a, _ = o.steps_ovf(8)\n"
            "snap = a.copy()\n"
            "for _ in range(5):\n"
            "    o.steps_ovf(4096)\n"
            "assert np.array_equal(a, snap), (a.tolist(), snap.tolist())\n"
            "x, _ = o.steps_ovf(4)\n"
            "y, _ = o.steps_ovf(4)\n"
            "assert x.ctypes.data != y.ctypes.data\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_repeated_calls_do_not_leak(self, built):
        """Each call allocates; the views must release their base arrays."""
        r = self._run(
            built,
            # ru_maxrss is KiB on Linux but BYTES on macOS — normalise in the
            # child, which knows its own platform. Reading it raw makes an
            # 800 KiB run look like an 800 MB leak on macOS only (and this
            # test did exactly that on its first CI run).
            "import resource, sys\n"
            "from p.nco import Nco\n"
            "unit = 1024 if sys.platform == 'darwin' else 1\n"
            "o = Nco()\n"
            "for _ in range(50):\n"
            "    o.steps_ovf(100000)\n"
            "base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "for _ in range(500):\n"
            "    o.steps_ovf(100000)\n"
            "end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "print((end - base) // unit)\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        growth_kb = int(r.stdout.strip())
        # 500 leaked 100k-element pairs (uint32 + uint8) would be ~250 MB; a
        # correct run is flat. The bar is loose enough for allocator noise on
        # any platform while still catching a per-call retention.
        assert growth_kb < 64 * 1024, f"grew {growth_kb} KiB — leaking"
