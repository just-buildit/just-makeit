"""gh-604: the variable_output reuse buffer grew without bound, and was slower.

The generated binding kept one per-instance output buffer, grew it on demand,
and — because a previously returned view might still alias it (gh-219) —
retired the old buffer to a freelist instead of freeing it, using a weakref to
detect that liveness (gh-437). Retired buffers were freed only in tp_dealloc.

The trap: **binding the result to a name is enough to make the view live**, so
an ordinary streaming loop took the retire path on every single call.

    for _ in range(3000):
        x = lo.steps(65536)        # previous x still alive at the next call

Measured on a generated project, before vs after:

    HOLD growth: 1547520 KiB   ->   448 KiB

~514 KiB per call retained for the object's lifetime. Dropping the result
instead of binding it gave 0 KiB growth either way, which is what pinned the
attribution to the `_view_live` branch.

The timing settled the design question — the reuse buffer was not merely
leaky, it was slower, because every call malloc'd a fresh buffer and touched
new pages against a monotonically growing heap:

    n=65536   hold: 96272 ns -> 20795 ns   (-78%)
    n=1048576 hold: 1502726 ns -> 239348 ns  (-84%)
    n=65536   drop: 20659 ns -> 20665 ns   (a wash)

So it cost a page fault per call to avoid an allocation that costs nothing at
DSP-realistic block sizes, and its failure mode when the precondition was not
met was "6x slower and growing" rather than "no speedup".

NumPy now owns each call's array. `out=` remains as the explicit
zero-allocation contract — and it is the one that can actually promise it,
since a caller-owned buffer cannot silently alias a previous result.

The `TestRuntime` growth check is the test that would have caught this: the
defect was invisible in the generated text (the retire logic read as correct,
and *was* correct — it just never freed) and only appears when a compiled
extension is called in a loop that keeps its results.
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
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

SINGLE = {
    "name": "steps",
    "arg_type": "void",
    "return_type": "float _Complex",
    "variable_output": True,
    "max_out": 65536,
}


def _ctx(**over):
    return make_methods_ctx("lo", "Lo", [dict(SINGLE, **over)])


class TestMachineryIsGone:
    def test_no_instance_storage_of_any_kind(self):
        ctx = _ctx()
        assert ctx["extra_buf_fields"] == ""
        assert ctx["extra_buf_alloc"] == ""
        assert ctx["extra_buf_free"] == ""

    def test_no_freelist_no_weakref_no_grow(self):
        src = _ctx()["extra_methods_c"]
        for gone in (
            "_retired",
            "_view_ref",
            "_view_live",
            "PyWeakref_NewRef",
            "PyWeakref_GetRef",
            "realloc(",
            "malloc(",
        ):
            assert gone not in src, gone

    def test_allocation_is_per_call_and_covers_n(self):
        src = _ctx()["extra_methods_c"]
        assert "size_t _need = (size_t)n;" in src
        assert "if (!_cap || _cap < _need) _cap = _need;" in src
        assert "PyArray_SimpleNew(1, &_adim, NPY_COMPLEX64)" in src

    def test_exact_fill_skips_the_trim_view(self):
        """The trim view is pure overhead when the kernel filled the whole
        allocation, which is the generator shape's normal case."""
        src = _ctx()["extra_methods_c"]
        assert "if ((size_t)n_out == _cap) {" in src
        assert "        return arr0;" in src

    def test_out_buffer_contract_survives(self):
        """out= is the remaining zero-allocation path and must still work."""
        src = _ctx()["extra_methods_c"]
        assert "if (out_obj && out_obj != Py_None) {" in src
        assert "PyArray_DATA(out_arr)" in src
        assert '"out has %zu elements, need >= %zu"' in src

    def test_nogil_keeps_numpy_out_of_the_released_section(self):
        """PyArray_DATA inlined into the kernel call would sit inside
        Py_BEGIN_ALLOW_THREADS; the data pointer is hoisted instead."""
        src = _ctx(nogil=True)["extra_methods_c"]
        body = src[src.index("Py_BEGIN_ALLOW_THREADS") :]
        body = body[: body.index("Py_END_ALLOW_THREADS")]
        assert "PyArray_" not in body
        assert "float complex *_d0 = (float complex *)PyArray_DATA(" in src


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
    for (size_t i = 0; i < n; i++) out[i] = (float)i + 0.0f * I;
    return n;"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestRuntime:
    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("gh604") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(dest, "lo", module=None, arg_type="void", no_step=True)
            method_run(
                dest,
                "lo",
                "steps",
                None,
                "void",
                "float _Complex",
                True,
                [],
                max_out=65536,
            )
        core = dest / "native/src/lo/lo_core.c"
        text = core.read_text("utf-8")
        stub = (
            "    (void)state;\n    (void)n;\n    (void)out;\n"
            "    return 0; /* placeholder */"
        )
        assert stub in text, "stub shape changed; update this test"
        core.write_text(text.replace(stub, KERNEL, 1), encoding="utf-8")

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

    def _run(self, dest, body):
        return subprocess.run(
            [sys.executable, "-c", body],
            cwd=dest,
            env={**os.environ, "PYTHONPATH": str(dest / "src")},
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_holding_the_result_does_not_grow_the_heap(self, built):
        """The headline defect: ~514 KiB retained per call, 1.5 GB over 3000."""
        r = self._run(
            built,
            # ru_maxrss is KiB on Linux, BYTES on macOS — normalise here,
            # where the platform is known.
            "import resource, sys\n"
            "from p.lo import Lo\n"
            "unit = 1024 if sys.platform == 'darwin' else 1\n"
            "rss = lambda: resource.getrusage("
            "resource.RUSAGE_SELF).ru_maxrss // unit\n"
            "o = Lo()\n"
            "o.steps(65536)\n"
            "base = rss()\n"
            "for _ in range(3000):\n"
            "    x = o.steps(65536)\n"  # bound -> the old retire path
            "print(rss() - base)\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        growth_kb = int(r.stdout.strip())
        # Pre-fix this measured 1_547_520 KiB. A correct run is a few hundred.
        assert growth_kb < 32 * 1024, f"grew {growth_kb} KiB — retaining"

    def test_results_are_correct_and_independent(self, built):
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.lo import Lo\n"
            "o = Lo()\n"
            "a = o.steps(4)\n"
            "b = o.steps(4)\n"
            "assert np.array_equal(a, np.arange(4, dtype=np.complex64))\n"
            "assert a.ctypes.data != b.ctypes.data\n"
            "for n in (1, 65535, 65536, 65537, 200000):\n"
            "    assert len(o.steps(n)) == n\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_out_buffer_still_zero_alloc_and_aliases_the_caller(self, built):
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.lo import Lo\n"
            "o = Lo()\n"
            "buf = np.zeros(65536, dtype=np.complex64)\n"
            "v = o.steps(1000, out=buf)\n"
            "assert len(v) == 1000\n"
            # The returned view must be a window onto the caller's memory.
            "assert v.ctypes.data == buf.ctypes.data\n"
            "assert buf[3] == 3\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_out_buffer_too_small_still_raises(self, built):
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.lo import Lo\n"
            "o = Lo()\n"
            "small = np.zeros(4, dtype=np.complex64)\n"
            "try:\n"
            "    o.steps(1000, out=small)\n"
            "except ValueError as e:\n"
            "    assert 'need >=' in str(e), e\n"
            "    print('ok')\n"
            "else:\n"
            "    raise AssertionError('no ValueError')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"
