"""A borrowed view must pin whatever keeps its memory alive.

`get_<name>_view()` on a fixed-size array state var returns a
`PyArray_SimpleNewFromData` window onto the component's state struct. It used
to pin **nothing** — no `PyArray_SetBaseObject`, no `Py_INCREF` — so the array
held no reference to the object whose memory it pointed at:

    o = Tap()
    v = o.get_coef_view()
    del o                      # state freed here
    v[0]                       # reads freed memory, silently

Demonstrated before the fix: `v.base` was `None`, and after `del o` the view
read `8.56e-10` where the state held `0`. No crash, no warning — just wrong
numbers from freed memory, which is the worst shape a memory bug can take.

Every other borrowed view in the generator pins something (the `buf_field`
property pins `self`); this one relied on a docstring. The fix pins `self`,
per the rule in `docs/memory-ownership.md`: *a borrowed view must pin whatever
keeps its memory alive*.

Note the guarantee has a documented limit that this file also pins: holding
the view keeps the **wrapper** alive, not the state. An explicit `destroy()`
frees the state while the base still only pins the wrapper, so a view read
after `destroy()` is still undefined. That is why the accessor is read-only
and says "Valid until destroy()".
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

from just_makeit._new import run as new_run


class TestGeneratedCode:
    @pytest.fixture
    def ext(self, tmp_path):
        dest = tmp_path / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest, ["tap"], [("coef", "float _Complex[8]", "")])
        return (dest / "native/src/tap/tap_ext.c").read_text("utf-8")

    def test_view_pins_self(self, ext):
        body = ext[ext.index("Tap_get_coef_view(") :]
        body = body[: body.index("\n}")]
        assert "Py_INCREF(self);" in body
        assert "PyArray_SetBaseObject(" in body
        assert "(PyObject *)self" in body

    def test_setbaseobject_failure_is_handled(self, ext):
        """SetBaseObject steals the ref, so a failure must undo the INCREF."""
        body = ext[ext.index("Tap_get_coef_view(") :]
        body = body[: body.index("\n}")]
        assert "Py_DECREF(self);" in body

    def test_view_stays_read_only(self, ext):
        body = ext[ext.index("Tap_get_coef_view(") :]
        body = body[: body.index("\n}")]
        assert "NPY_ARRAY_WRITEABLE" in body

    def test_copy_getter_does_not_pin(self, ext):
        """get_<name>() copies, so it must NOT pin the object."""
        body = ext[ext.index("Tap_get_coef(") :]
        body = body[: body.index("\n}")]
        assert "PyArray_SetBaseObject" not in body


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
    """The only form that catches this — it needs a real free()."""

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        dest = tmp_path_factory.mktemp("stateview") / "p"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p", dest, ["tap"], [("coef", "float _Complex[8]", "")])
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

    def test_view_survives_dropping_the_object(self, built):
        r = self._run(
            built,
            "import gc\n"
            "import numpy as np\n"
            "from p.tap import Tap\n"
            "o = Tap()\n"
            "v = o.get_coef_view()\n"
            "assert v.base is o, v.base\n"
            "del o\n"
            "gc.collect()\n"
            # Pre-fix this read freed memory and returned garbage.
            "assert np.array_equal(v, np.zeros(8, np.complex64)), v\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_view_tracks_writes_through_the_setter(self, built):
        """It is a live window, not a snapshot — that's the point of it."""
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.tap import Tap\n"
            "o = Tap()\n"
            "v = o.get_coef_view()\n"
            "o.set_coef(np.arange(8, dtype=np.complex64))\n"
            "assert np.array_equal(v, np.arange(8, dtype=np.complex64)), v\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_view_is_not_writable(self, built):
        r = self._run(
            built,
            "from p.tap import Tap\n"
            "v = Tap().get_coef_view()\n"
            "assert not v.flags.writeable\n"
            "try:\n"
            "    v[0] = 1\n"
            "except ValueError:\n"
            "    print('ok')\n"
            "else:\n"
            "    raise AssertionError('view was writable')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"

    def test_copy_getter_is_independent(self, built):
        """The copy accessor must not be affected by later state changes."""
        r = self._run(
            built,
            "import numpy as np\n"
            "from p.tap import Tap\n"
            "o = Tap()\n"
            "c = o.get_coef()\n"
            "assert c.base is None\n"
            "o.set_coef(np.arange(8, dtype=np.complex64))\n"
            "assert np.array_equal(c, np.zeros(8, np.complex64)), c\n"
            "print('ok')\n",
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert r.stdout.strip() == "ok"
