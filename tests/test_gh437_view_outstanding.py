"""gh-437: the returned view must survive later calls of the same size.

Original defect: the generated variable_output binding returned a view of an
internal grow-on-demand buffer but retired that buffer only when it *grew*, so
a same-size (or smaller) next call reused it in place and silently overwrote
any outstanding view — a caller accumulating returned chunks got the last
call's data in every earlier chunk.

The original fix kept a weakref to the last returned view and, when it was
still alive, retired the buffer and allocated fresh exactly like a grow.

**gh-604 removed that machinery entirely** — along with the reuse buffer and
the gh-219 retired freelist — because measurement showed the retire path
(which any loop that *binds* its result takes) grew RSS by ~514 KiB per call
and ran 6-8x slower than simply letting NumPy own each call's array. With
nothing shared between calls, this defect is no longer fixed but
**structurally impossible**.

So this file no longer asserts the mechanism, which is gone. It asserts the
guarantee the mechanism existed to provide, at runtime, in the exact shape the
issue reported: accumulate chunks, then check the early ones still hold their
own data. That assertion is implementation-independent — it held before
gh-604, it holds now, and it would fail again if a future change reintroduced
a shared buffer without liveness tracking.
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

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["nco"], [("freq", "double", "0.0")])
    return dest


class TestNothingIsShared:
    """The mechanism is gone because the hazard cannot arise."""

    def _ext(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        return (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def test_no_weakref_tracking_remains(self, project):
        ext = self._ext(project)
        assert "_view_ref" not in ext
        assert "PyWeakref_NewRef" not in ext
        assert "_view_live" not in ext

    def test_no_reuse_buffer_or_freelist_remains(self, project):
        ext = self._ext(project)
        assert "_execute_cf32_buf" not in ext
        assert "_retired" not in ext

    def test_each_call_allocates_its_own_output(self, project):
        ext = self._ext(project)
        assert "PyArray_SimpleNew(1, &_adim, NPY_COMPLEX64)" in ext

    def test_out_path_still_writes_the_callers_buffer(self, project):
        """out= is unchanged: their array, their lifetime, zero alloc."""
        ext = self._ext(project)
        # Scope to execute_cf32's own wrapper — the object's builtin
        # steps(x, out=) has its own out= branch earlier in the file, and a
        # file-wide slice would span both.
        wrapper = ext[ext.index("Nco_execute_cf32(NcoObject") :]
        out_branch = wrapper[
            wrapper.index("out_obj && out_obj != Py_None") : wrapper.index(
                "size_t _need"
            )
        ]
        assert "PyArray_DATA(out_arr)" in out_branch
        assert "PyArray_SimpleNew(" not in out_branch


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

# Writes a per-call constant so an overwritten chunk is instantly visible.
KERNEL = """    (void)state;
    for (size_t i = 0; i < n; i++) out[i] = (float)state->freq + 0.0f * I;
    state->freq += 1.0;
    return n;"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestAccumulatedChunksStayIndependent:
    """The guarantee itself — the shape the issue actually reported."""

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("gh437") / "dsp"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("dsp", dest, ["nco"], [("freq", "double", "0.0")])
            method_run(
                dest,
                "nco",
                "execute_cf32",
                None,
                "void",
                "float _Complex",
                True,
                [],
                max_out=4096,
            )
        core = dest / "native/src/nco/nco_core.c"
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
            timeout=180,
        )

    def test_same_size_calls_do_not_overwrite_earlier_chunks(self, built):
        """Each chunk keeps its own call's value, not the last call's."""
        r = self._run(
            built,
            "from dsp.nco import Nco\n"
            "o = Nco()\n"
            # Same size every time -- the exact case that used to reuse the
            # buffer in place and corrupt every earlier chunk.
            "chunks = [o.execute_cf32(1024) for _ in range(8)]\n"
            "vals = [c[0].real for c in chunks]\n"
            "assert len(set(vals)) == 8, vals\n"
            "assert vals == sorted(vals), vals\n"
            "assert all(len(c) == 1024 for c in chunks)\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_chunks_do_not_alias_each_other(self, built):
        r = self._run(
            built,
            "from dsp.nco import Nco\n"
            "o = Nco()\n"
            "a = o.execute_cf32(512)\n"
            "b = o.execute_cf32(512)\n"
            "assert a.ctypes.data != b.ctypes.data\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"
