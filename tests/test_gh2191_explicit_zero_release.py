"""gh-2191: a release's explicit count of 0 is a count, not an omission.

`release_resolve_c` (gh-1426 A) tested the parsed local -- `if (!n)` -- and
0 is both the render-time default and a count a caller can mean. So an
explicit `consume(0)` took the OUTSTANDING borrow's count: doppler's
`peek(64); consume(0)` released 64 samples nobody had processed
(doppler-dsp/doppler#2014), and `consume(0)` with nothing outstanding raised
where the C release would have done nothing.

Omitted is now decided by PRESENCE -- the count's position in `args`, or its
name in `kwds` -- and an explicit count, 0 included, reaches C as given.

GATE: built and run. A ring records the count its release was called with;
      an explicit 0 must arrive as 0, with and without a borrow outstanding,
      positionally and by keyword, while a bare release still takes the
      borrow's count and still refuses when there is none.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from just_makeit import _borrow  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()

_CONSUME = {
    "name": "consume",
    "releases": ["wait"],
    "params": [{"name": "n", "type": "size_t"}],
}


class TestPresenceNotValue:
    def test_an_omitted_count_is_looked_for_where_the_parse_found_it(self):
        c = _borrow.release_resolve_c(_CONSUME, kwlist=["n"])
        assert "PyTuple_GET_SIZE(args) > 0" in c
        assert 'PyDict_GetItemString(kwds, "n")' in c
        # the VALUE is no longer the test: an explicit 0 must reach C
        assert "if (!n) {\n        n = self->" not in c

    def test_the_position_is_the_kwlist_index(self):
        """A release that also takes the input `x` parses `[x, n]`."""
        c = _borrow.release_resolve_c(_CONSUME, kwlist=["x", "n"])
        assert "PyTuple_GET_SIZE(args) > 1" in c

    def test_a_named_release_count_among_several(self):
        m = {
            "name": "consume",
            "releases": ["wait"],
            "release_count": "k",
            "params": [{"name": "flags"}, {"name": "k"}],
        }
        c = _borrow.release_resolve_c(m, kwlist=["flags", "k"])
        assert "PyTuple_GET_SIZE(args) > 1" in c
        assert 'PyDict_GetItemString(kwds, "k")' in c

    def test_a_release_with_no_count_only_clears(self):
        m = {"name": "reset", "releases": ["wait"]}
        assert _borrow.release_resolve_c(m, kwlist=[]) == (
            f"    self->{_borrow.RELEASE_FIELD} = 0;\n"
        )

    def test_the_kwlist_is_required(self):
        """A caller that forgets it fails at once, not with a wrong index."""
        with pytest.raises(TypeError):
            _borrow.release_resolve_c(_CONSUME)  # type: ignore[call-arg]


@pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")
class TestBuiltAndRun:
    WAIT = """    if (n > 8) return NULL;
    return state->buf;"""
    CONSUME = """    state->last = n;
    state->calls++;"""

    @classmethod
    def _built(cls, root: Path) -> None:
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        _silent(
            method_run,
            root,
            "ring",
            "wait",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("n", "size_t")],
            borrow=True,
        )
        _silent(
            method_run,
            root,
            "ring",
            "consume",
            None,
            "void",
            "void",
            False,
            [],
            params=[("n", "size_t")],
            releases=["wait"],
        )
        for name in ("last", "calls"):
            _silent(
                method_run,
                root,
                "ring",
                name,
                None,
                "void",
                "size_t",
                False,
                [],
            )
        h = root / INC_ROOT / "ring/ring_core.h"
        h.write_text(
            h.read_text().replace(
                "    size_t cap;",
                "    size_t cap;\n    float _Complex buf[8];\n"
                "    size_t last;\n    size_t calls;",
                1,
            )
        )
        c = root / "native/src/ring/ring_core.c"
        text = c.read_text()
        bodies = {
            "ring_wait(ring_state_t *state, size_t n)": cls.WAIT,
            "ring_consume(ring_state_t *state, size_t n)": cls.CONSUME,
            "ring_last(ring_state_t *state)": "    return state->last;",
            "ring_calls(ring_state_t *state)": "    return state->calls;",
        }
        for sig, body in bodies.items():
            i = text.index(sig)
            j = text.index("\n}\n", i)
            start = text.index("{", i) + 1
            text = text[:start] + "\n" + body + text[j:]
        c.write_text(text)
        for cmd in (
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            ["cmake", "--build", str(root / "build"), "--target", "ring"],
        ):
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=900
            )
            assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_an_explicit_zero_reaches_c_as_zero(self, tmp_path):
        root = tmp_path / "p"
        self._built(root)
        probe = root / "probe.py"
        probe.write_text(
            "from p.ring import Ring\n"
            "r = Ring(cap=8)\n"
            # a borrow outstanding: an explicit 0 releases NOTHING
            "v = r.wait(4)\n"
            "r.consume(0)\n"
            "assert (r.last(), r.calls()) == (0, 1), (r.last(), r.calls())\n"
            # nothing outstanding: still a count, so no RuntimeError
            "r.consume(0)\n"
            "assert (r.last(), r.calls()) == (0, 2), (r.last(), r.calls())\n"
            # by keyword, too
            "v = r.wait(2)\n"
            "r.consume(n=0)\n"
            "assert (r.last(), r.calls()) == (0, 3), (r.last(), r.calls())\n"
            # omitted: the outstanding borrow's count, as before
            "v = r.wait(3)\n"
            "r.consume()\n"
            "assert (r.last(), r.calls()) == (3, 4), (r.last(), r.calls())\n"
            # omitted with nothing outstanding: still refused, never reaching C
            "try:\n"
            "    r.consume()\n"
            "    raise SystemExit('a bare release with nothing out did not raise')\n"
            "except RuntimeError:\n"
            "    pass\n"
            "assert r.calls() == 4, r.calls()\n"
            # an explicit nonzero count is passed as given
            "v = r.wait(5)\n"
            "r.consume(2)\n"
            "assert (r.last(), r.calls()) == (2, 5), (r.last(), r.calls())\n"
            "print('OK')\n"
        )
        out = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(root),
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "OK" in out.stdout, out.stdout
