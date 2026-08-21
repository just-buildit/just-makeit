"""gh-1085: an all-scalar variable_output method must refuse a zero bound.

Every `variable_output` binding allocates behind a floor::

    size_t _need = <the call's own length>;
    size_t _cap  = <m>_max_out(...);
    if (!_cap || _cap < _need) _cap = _need;

and that floor is what makes `max_out()` returning **0** safe — jm's own docs
call a zero legal and say the binding then sizes the allocation from the call
itself.

For every shape but one, `_need` is an independent quantity. **An
all-scalar-params method has no call length**, so gh-607 made `_need` fall back
to `max_out()` itself — and the two sides of the floor became the same
expression, leaving a guard that cannot fire.

Measured before the fix, compiled and run: a kernel writing four floats behind
the scaffolded `return 0;` got a zero-length array, wrote past it, and the
caller received `[0. 0. 0. 0.]` — right shape, values lost, no error, because
`PyArray_Resize` had reallocated underneath. At 4096 samples glibc aborted with
`realloc(): invalid next size`.

`TestItActuallyRuns` is the load-bearing class and the reason this file compiles
rather than inspects: the emitted C reads fine either way, and only running it
distinguishes "raised" from "corrupted the heap and returned the right shape".

The scope is the other half. A zero bound is still **legal and rescued** for
every shape that has a real length to fall back on, and refusing there would
break a documented contract — so `TestOnlyTheShapeWithNoFloor` asserts the
guard is absent from those, and runs one to prove the old behaviour survives.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_JM = shutil.which("just-makeit") or shutil.which("jm")
_NO_TOOLCHAIN = (
    _JM is None
    or shutil.which("cmake") is None
    or (shutil.which("cc") is None and shutil.which("gcc") is None)
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path, name: str, **method_kw) -> Path:
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "dly",
        module=None,
        arg_type="float",
        return_type="float",
        state_vars=[("n", "size_t", "16")],
    )
    _quiet(
        method_run,
        root,
        "dly",
        "push",
        None,
        return_type="float",
        variable_output=True,
        multi_output=[],
        **method_kw,
    )
    return root


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "dly" / "dly_ext.c").read_text(
        encoding="utf-8"
    )


ALL_SCALAR = dict(arg_type="void", params=[("x", "double")])


class TestTheGuardIsEmittedWhereTheFloorIsInert:
    def test_the_all_scalar_shape_gets_it(self, tmp_path):
        root = _project(tmp_path, "g", **ALL_SCALAR)
        assert "cannot size its output" in _ext(root)

    def test_it_names_the_method_and_the_function_to_implement(self, tmp_path):
        """A message that does not say what to do is a crash with extra
        steps. Both names are derived, not guessed by the reader."""
        text = _ext(_project(tmp_path, "g", **ALL_SCALAR))
        assert '"push", "dly_push_max_out"' in text

    def test_it_precedes_the_allocation(self, tmp_path):
        """Refusing after `PyArray_SimpleNew` would still hand the kernel a
        zero-length buffer, which is the whole defect.

        Scoped to `push`'s own wrapper: the file also holds `step`/`steps`,
        whose allocations come earlier, so a whole-file index would compare
        this guard against an unrelated method's.
        """
        text = _ext(_project(tmp_path, "g", **ALL_SCALAR))
        i = text.index("\nDly_push(")
        block = text[i : text.index("\n}\n", i)]
        assert "cannot size its output" in block
        assert block.index("cannot size its output") < block.index(
            "PyArray_SimpleNew"
        )


class TestOnlyTheShapeWithNoFloor:
    """A zero bound stays legal wherever there is a length to fall back on.

    `max_out() == 0` meaning "unknown" is a documented contract, and gating
    those shapes would break it — a false refusal here is worse than the bug,
    because it fails a call that works today.
    """

    @pytest.mark.parametrize(
        "label,kw",
        [
            ("array-arg", dict(arg_type="float", params=[])),
            ("generator", dict(arg_type="void", params=[])),
            (
                "one-array-param",
                dict(arg_type="void", params=[("x", "float[]")]),
            ),
        ],
    )
    def test_no_guard(self, tmp_path, label, kw):
        assert "cannot size its output" not in _ext(
            _project(tmp_path, "g", **kw)
        )


def _implement(root: Path, max_out_body: str, n_write: int) -> None:
    """Give the scaffold a kernel that writes, and a chosen `max_out`."""
    p = root / "native" / "src" / "dly" / "dly_core.c"
    s = p.read_text(encoding="utf-8")
    s = s.replace("    return 0; /* placeholder */", max_out_body, 1)
    sig = "dly_push(dly_state_t *state, double x, float *out)"
    tail = s[s.index(sig) :]
    b0 = tail.index("{")
    b1 = tail.index("\n}", b0)
    s = s.replace(
        tail[b0:b1],
        "{\n"
        "    (void)state; (void)x;\n"
        f"    for (int i = 0; i < {n_write}; i++)"
        " out[i] = (float)(i + 1);\n"
        f"    return {n_write};",
        1,
    )
    p.write_text(s, encoding="utf-8")


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestItActuallyRuns:
    """The oracle with no opinions.

    Before the fix this exact project aborted the interpreter with
    ``realloc(): invalid next size``. Inspecting the emitted C cannot tell you
    that; only running it can, which is why this class builds.
    """

    @staticmethod
    def _run(root: Path, snippet: str) -> subprocess.CompletedProcess:
        # The installed console script, not `python -m just_makeit._cli` —
        # that module has no `__main__` guard, so it exits 0 having built
        # nothing and the import below fails on a `.so` that never existed.
        # A green `check=True` over a command that did nothing is exactly the
        # shape this repo keeps finding.
        built = subprocess.run(
            [_JM, "build"], cwd=root, capture_output=True, text=True
        )
        assert built.returncode == 0, built.stdout + built.stderr
        return subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=root / "src",
            capture_output=True,
            text=True,
        )

    def test_a_zero_bound_raises_instead_of_corrupting_the_heap(
        self, tmp_path
    ):
        root = _project(tmp_path, "zb", **ALL_SCALAR)
        _implement(root, "    return 0;", 4096)
        proc = self._run(
            root,
            "from zb.dly import Dly\n"
            "try:\n"
            "    Dly().push(1.0)\n"
            "    print('NO-ERROR')\n"
            "except RuntimeError as e:\n"
            "    print('RAISED', e)\n",
        )
        # A crash is `returncode != 0` with nothing on stdout — the state this
        # replaces. Asserting the message alone would pass on a segfault that
        # happened to print first.
        assert proc.returncode == 0, proc.stderr
        assert "RAISED" in proc.stdout, proc.stdout
        assert "dly_push_max_out" in proc.stdout

    def test_the_out_branch_refuses_a_zero_bound_too(self, tmp_path):
        """gh-1079 gave this shape an `out=` buffer; the guard has to cover
        both paths, and it nearly did not.

        The `out=` branch does not allocate, so the allocation-path guard
        does not reach it — and there `_cap` is the CALLER'S buffer size, not
        the bound. A guard testing `_cap` asks "did you pass an empty array?"
        instead of "can jm size this at all?", so a 4096-element buffer
        sailed through against a zero bound and the kernel ran. Measured by
        hand, then missed by every test in this file until this one, because
        sabotaging the bound variable left the suite green.
        """
        root = _project(tmp_path, "ob", **ALL_SCALAR)
        _implement(root, "    return 0;", 100)
        proc = self._run(
            root,
            "import numpy as np\n"
            "from ob.dly import Dly\n"
            "buf = np.zeros(4096, dtype=np.float32)\n"
            "try:\n"
            "    Dly().push(1.0, out=buf)\n"
            "    print('NO-ERROR')\n"
            "except RuntimeError as e:\n"
            "    print('RAISED', e)\n",
        )
        assert proc.returncode == 0, proc.stderr
        assert "RAISED" in proc.stdout, proc.stdout
        assert "dly_push_max_out" in proc.stdout

    def test_a_real_bound_fills_the_callers_buffer(self, tmp_path):
        """The other half: with a real bound, `out=` is zero-alloc and the
        returned array is a VIEW of what the caller passed.

        Checked through the caller's own buffer, not just the return value —
        a fix that quietly allocated its own array would satisfy a shape
        assertion and defeat the entire point of the feature.
        """
        root = _project(tmp_path, "of", **ALL_SCALAR)
        _implement(root, "    return 4096;", 100)
        proc = self._run(
            root,
            "import numpy as np\n"
            "from of.dly import Dly\n"
            "o = Dly()\n"
            "buf = np.zeros(o.push_max_out(), dtype=np.float32)\n"
            "r = o.push(1.0, out=buf)\n"
            "print(r.shape[0], r[-1], buf[0], buf[99])\n",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split() == ["100", "100.0", "1.0", "100.0"], (
            proc.stdout
        )

    def test_a_short_buffer_is_refused(self, tmp_path):
        """The bound is checked, not merely computed."""
        root = _project(tmp_path, "sb", **ALL_SCALAR)
        _implement(root, "    return 4096;", 100)
        proc = self._run(
            root,
            "import numpy as np\n"
            "from sb.dly import Dly\n"
            "try:\n"
            "    Dly().push(1.0, out=np.zeros(8, dtype=np.float32))\n"
            "    print('ACCEPTED')\n"
            "except ValueError as e:\n"
            "    print('REFUSED', e)\n",
        )
        assert proc.returncode == 0, proc.stderr
        assert "REFUSED" in proc.stdout, proc.stdout
        assert "need >= 4096" in proc.stdout

    def test_a_real_bound_still_returns_the_data(self, tmp_path):
        """The guard against over-refusing, and it checks the VALUES.

        A fix that refused everything would satisfy the test above. A fix
        that allocated but truncated would satisfy a shape check. Reading
        the last element is what distinguishes both from working.
        """
        root = _project(tmp_path, "rb", **ALL_SCALAR)
        _implement(root, "    return 4096;", 4096)
        proc = self._run(
            root,
            "from rb.dly import Dly\n"
            "r = Dly().push(1.0)\n"
            "print(r.shape[0], r[0], r[-1])\n",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split() == ["4096", "1.0", "4096.0"], proc.stdout

    def test_a_zero_bound_is_still_fine_where_a_length_exists(self, tmp_path):
        """The documented contract, kept. An `arg_type` method whose
        `max_out()` returns 0 sizes from the input, exactly as before."""
        root = tmp_path / "ok"
        _quiet(new_run, "ok", root)
        _quiet(
            object_run,
            root,
            "flt",
            module=None,
            arg_type="float",
            return_type="float",
            state_vars=[("n", "size_t", "16")],
        )
        _quiet(
            method_run,
            root,
            "flt",
            "run",
            None,
            arg_type="float",
            return_type="float",
            variable_output=True,
            multi_output=[],
        )
        core = root / "native" / "src" / "flt" / "flt_core.c"
        s = core.read_text(encoding="utf-8")
        sig = "flt_run(flt_state_t *state, const float *in, size_t n_in, float *out)"
        tail = s[s.index(sig) :]
        b0 = tail.index("{")
        b1 = tail.index("\n}", b0)
        core.write_text(
            s.replace(
                tail[b0:b1],
                "{\n    (void)state;\n"
                "    for (size_t i = 0; i < n_in; i++) out[i] = in[i] * 2.0f;\n"
                "    return n_in;",
                1,
            ),
            encoding="utf-8",
        )
        proc = self._run(
            root,
            "import numpy as np\n"
            "from ok.flt import Flt\n"
            "r = Flt().run(np.ones(8, dtype=np.float32))\n"
            "print(r.shape[0], r[0])\n",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split() == ["8", "2.0"], proc.stdout
