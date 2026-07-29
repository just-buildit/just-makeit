"""gh-581: an ``out=`` buffer must never be silently cast into a temporary.

Passing ``out=`` means "write into THIS array, do not allocate". Marshaling it
with a bare ``PyArray_FROM_OTF(out_obj, NPY_X, … | NPY_ARRAY_WRITEABLE)`` breaks
that promise whenever the dtype does not already match: ``FROM_OTF`` casts into
a NEW temporary, the kernel fills the temporary, and the temporary is freed on
the way out. The call returns a correct-looking result while the caller's buffer
is never touched — invisible to anyone who only reads the return value.

The handle generator (:mod:`_handle`, shape (d)) and the capsule generator
(:mod:`_capsule`) already guarded against this; the object generator's ``out=``
paths and the module-function generator's ``out`` params did not, so the same
class silently lost the check when its ``kind`` changed. The guard now lives
once in :func:`_coerce.out_buffer_guard` and every generator emits it.

Dtype is not the only trigger. ``FROM_OTF`` is also asked for
``NPY_ARRAY_C_CONTIGUOUS``, so a **strided** same-dtype array passes the dtype
check and is copied anyway. Reproduced on a built extension::

    big = np.zeros((4, 2), np.float32)
    g.steps(np.arange(4, dtype=np.float32), out=big[:, 0])
    big[:, 0]        # -> [0. 0. 0. 0.]  never written; the return was a copy

The guard therefore requires dtype **and** contiguity. Alignment is not checked
— see the note in :mod:`_coerce` for why it would reject nothing.

The load-bearing test here is :class:`TestNoUnguardedWritableMarshal`, which
asserts the *invariant* rather than today's site list: any future ``out=`` path
that forgets the guard fails it without anyone having to remember gh-581.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _coerce  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

# A caller-supplied buffer is marshaled with the WRITEABLE flag; the guard is
# the PyArray_Check/PyArray_TYPE/IS_C_CONTIGUOUS/ISWRITEABLE quad that must
# precede it.
WRITABLE_MARSHAL = re.compile(
    r"PyArray_FROM_OTF\(\s*[^;]*?NPY_ARRAY_WRITEABLE", re.S
)
GUARD_HEAD = "if (!PyArray_Check("
# The message is emitted as two adjacent C literals to stay inside 79 chars;
# this is the first, which carries the caller's label.
ERR_MSG = "must be a writable, C-contiguous"


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run(
        "dsp",
        dest,
        ["nco"],
        [("freq", "double", "0.0")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return dest


def _ext(project, obj="nco"):
    return (project / "native" / "src" / obj / f"{obj}_ext.c").read_text(
        encoding="utf-8"
    )


class TestEmitter:
    """The shared emitter itself — one primitive, no per-generator copies."""

    def test_shape(self):
        g = _coerce.out_buffer_guard("out_obj", "NPY_COMPLEX64")
        assert GUARD_HEAD + "out_obj)" in g
        assert "PyArray_TYPE((PyArrayObject *)out_obj) != NPY_COMPLEX64" in g
        assert "!PyArray_ISWRITEABLE((PyArrayObject *)out_obj)" in g
        assert f'"out {ERR_MSG}"' in g
        assert g.endswith("}\n")

    def test_rejects_a_strided_buffer(self):
        """Contiguity is checked, not just dtype.

        ``FROM_OTF`` is asked for ``NPY_ARRAY_C_CONTIGUOUS``, so a strided
        same-dtype array passes the dtype check and is then *copied* — the
        caller's buffer is never written. Same silent failure as gh-581, a
        different trigger.
        """
        g = _coerce.out_buffer_guard("out_obj", "NPY_COMPLEX64")
        assert "!PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj)" in g

    def test_decrefs_release_before_return(self):
        """Anything already owned is released on the reject path."""
        g = _coerce.out_buffer_guard(
            "out_obj", "NPY_FLOAT32", decrefs="Py_DECREF(in_arr);"
        )
        assert g.index("Py_DECREF(in_arr);") < g.index("return NULL;")

    def test_label_names_the_users_argument(self):
        g = _coerce.out_buffer_guard("dst_obj", "NPY_FLOAT32", label="dst")
        assert f'"dst {ERR_MSG}"' in g

    @pytest.mark.parametrize("indent", [4, 8])
    def test_fits_line_budget_at_either_depth(self, indent):
        """79-char project style, at function scope and inside an if-branch."""
        g = _coerce.out_buffer_guard(
            "out_obj", "NPY_COMPLEX64", indent=" " * indent
        )
        assert all(len(ln) <= 79 for ln in g.splitlines())
        assert all(ln.startswith(" " * indent) for ln in g.splitlines())


class TestObjectSteps:
    """The built-in ``steps(x, out=)`` path (the shape doppler hit)."""

    def test_guard_precedes_marshal(self, project):
        ext = _ext(project)
        branch = ext[ext.index("out_obj && out_obj != Py_None") :]
        assert branch.index(GUARD_HEAD) < branch.index("PyArray_FROM_OTF")

    def test_requires_exact_output_dtype(self, project):
        ext = _ext(project)
        assert "PyArray_TYPE((PyArrayObject *)out_obj) != NPY_COMPLEX64" in ext

    def test_requires_a_contiguous_buffer(self, project):
        ext = _ext(project)
        assert "!PyArray_IS_C_CONTIGUOUS((PyArrayObject *)out_obj)" in ext

    def test_releases_input_on_reject(self, project):
        """The input array is already owned when the guard runs."""
        ext = _ext(project)
        guard = ext[ext.index(GUARD_HEAD) :]
        assert guard.index("Py_DECREF(in_arr);") < guard.index("return NULL;")


class TestMethodOutPaths:
    """Both method ``out=`` flavors — fixed-size batch and variable_output."""

    def test_batch_method(self, project):
        method_run(
            project,
            "nco",
            "gain",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            batch=True,
        )
        assert GUARD_HEAD in _ext(project)

    def test_variable_output_method(self, project):
        method_run(
            project,
            "nco",
            "decim",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        ext = _ext(project)
        branch = ext[ext.index("out_obj && out_obj != Py_None") :]
        assert branch.index(GUARD_HEAD) < branch.index("PyArray_FROM_OTF")


class TestFunctionOutParam:
    """The module-function generator's ``out``-marked array param."""

    def test_guard_emitted(self, project):
        module_run(project, "ops")
        function_run(
            project,
            "scale",
            "ops",
            params=[("x", "float[]", False), ("y", "float[]", True)],
            return_type="void",
        )
        src = (project / "native" / "src" / "ops" / "ops_ext.c").read_text(
            encoding="utf-8"
        )
        assert GUARD_HEAD + "y_obj)" in src
        # the message names the param as the user typed it, not a generic "out"
        assert f'"y {ERR_MSG}"' in src
        assert src.index(GUARD_HEAD) < src.index("NPY_ARRAY_WRITEABLE")


class TestNoUnguardedWritableMarshal:
    """The invariant: no writable marshal anywhere without a preceding guard.

    This is what protects future generators — a new ``out=`` path that forgets
    the guard fails here even though it is not named anywhere in this file.
    """

    def _sources(self, project):
        method_run(
            project,
            "nco",
            "decim",
            None,
            "float _Complex",
            "float _Complex",
            True,
            [],
        )
        method_run(
            project,
            "nco",
            "gain",
            None,
            "float _Complex",
            "float _Complex",
            False,
            [],
            batch=True,
        )
        module_run(project, "ops")
        function_run(
            project,
            "scale",
            "ops",
            params=[("x", "float[]", False), ("y", "float[]", True)],
            return_type="void",
        )
        return sorted((project / "native").rglob("*_ext.c"))

    def test_every_writable_marshal_is_guarded(self, project):
        srcs = self._sources(project)
        assert srcs, "fixture produced no extension sources"
        seen = 0
        for path in srcs:
            text = path.read_text(encoding="utf-8")
            for m in WRITABLE_MARSHAL.finditer(text):
                seen += 1
                # The guard is emitted immediately above its marshal, so it is
                # the LAST guard occurring before this match.
                before = text[: m.start()]
                assert GUARD_HEAD in before, (
                    f"{path.name}: writable marshal at offset {m.start()} "
                    "has no preceding exact-dtype guard (gh-581)"
                )
                between = before[before.rindex(GUARD_HEAD) :]
                assert ERR_MSG in between, (
                    f"{path.name}: the guard nearest the writable marshal at "
                    f"offset {m.start()} is not an out-buffer guard (gh-581)"
                )
        assert seen, "no writable out= marshal generated — fixture is stale"


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


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestRuntime:
    """Compiled proof, because the defect is invisible in the generated text.

    The unguarded form reads as correct C and returns correct *values* — only a
    caller who reads their own buffer afterwards ever notices.
    """

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("gh581") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest)
            object_run(
                dest,
                "gain",
                module=None,
                arg_type="float",
                return_type="float",
            )
        # Make step() do something observable, so "was the caller's buffer
        # written?" is distinguishable from "was it left at zero?".
        core = dest / "native/inc/gain/gain_core.h"
        text = core.read_text("utf-8")
        stub = "    return (float)x;"
        assert stub in text, "stub shape changed; update this test"
        core.write_text(
            text.replace(stub, "    return (float)x * 2.0f;", 1),
            encoding="utf-8",
        )

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

    def test_contiguous_out_is_written_in_place(self, built):
        """The control: the contract still works for the intended input."""
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.gain import Gain\n"
            "g = Gain()\n"
            "x = np.arange(4, dtype=np.float32)\n"
            "out = np.zeros(4, dtype=np.float32)\n"
            "v = g.steps(x, out=out)\n"
            "assert v is out, 'out= returned a different array'\n"
            "assert np.array_equal(out, [0, 2, 4, 6]), out\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_strided_out_raises_instead_of_being_silently_copied(self, built):
        """The defect: a column of a 2-D buffer is writable and the right
        dtype, so only the contiguity check stands between the caller and a
        silently discarded result."""
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.gain import Gain\n"
            "g = Gain()\n"
            "x = np.arange(4, dtype=np.float32)\n"
            "big = np.zeros((4, 2), dtype=np.float32)\n"
            "col = big[:, 0]\n"
            "assert not col.flags['C_CONTIGUOUS']\n"
            "assert col.flags['WRITEABLE']\n"
            "assert col.dtype == np.float32\n"
            "try:\n"
            "    g.steps(x, out=col)\n"
            "except TypeError as e:\n"
            "    assert 'C-contiguous' in str(e), e\n"
            "else:\n"
            "    raise AssertionError(\n"
            "        'strided out= accepted; big[:, 0] = %r' % (big[:, 0],))\n"
            "assert np.array_equal(big[:, 0], [0, 0, 0, 0])\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"
