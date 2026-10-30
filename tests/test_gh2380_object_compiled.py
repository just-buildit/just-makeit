"""gh-2380: the object kind's setters and ``step()``, compiled and run.

The scan in ``test_gh2380_order_scan_object`` reads the generated C. This
is the truth anchor behind it: a scan of text can have a false negative, a
process killed by SIGSEGV cannot. An argument whose conversion runs Python
(``__index__`` / ``__complex__`` / ``__array__``) destroys the object; the
call must then raise ``RuntimeError``, not hand the kernel a NULL state.
"""

# gh-1591: hand-written C spells jm's bare derived names.

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

_PROBE = """\
import numpy as np
from p import W

out = []


def attempt(label, call):
    w = W(k=1)
    try:
        call(w)
        out.append(label + ": returned")
    except RuntimeError as e:
        out.append(label + ": RuntimeError " + str(e))


class Idx:
    def __init__(self, w):
        self.w = w

    def __index__(self):
        self.w.destroy()
        return 9


class Cplx:
    def __init__(self, w):
        self.w = w

    def __complex__(self):
        self.w.destroy()
        return 1 + 0j


class Arr:
    def __init__(self, w):
        self.w = w

    def __array__(self, dtype=None, copy=None):
        self.w.destroy()
        return np.zeros(4, dtype=np.float32)


class Flt:
    def __init__(self, w):
        self.w = w

    def __float__(self):
        self.w.destroy()
        return 1.5


attempt("gain", lambda w: setattr(w, "gain", Flt(w)))
attempt("set_k", lambda w: w.set_k(Idx(w)))
attempt("step", lambda w: w.step(Cplx(w)))
attempt("set_taps", lambda w: w.set_taps(Arr(w)))
print("\\n".join(out))
"""


def _ok(*argv: str, cwd: Path) -> None:
    r = run_cli(*argv, cwd=cwd)
    assert r.returncode == 0, (argv, r.stdout, r.stderr)


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_teardown_in_conversion_is_an_error_not_a_crash(tmp_path):
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok(
        "object", "w", "--state", "k:int:0", "--state", "taps:float[4]",
        cwd=proj,
    )  # fmt: skip
    _ok(
        "property", "w", "gain", "--type", "double", "--field", "--writable",
        cwd=proj,
    )  # fmt: skip
    # The scaffolded setters write the state, so a NULL handed to one
    # crashes; the scaffolded step() never touches it, so it reads it as a
    # real kernel does.
    head = proj / "native" / "inc" / "p" / "w" / "w_core.h"
    text = head.read_text(encoding="utf-8")
    text, hits = re.subn(
        r"(\nw_step\(const w_state_t \*state[^)]*\)\n\{\n)",
        r"\1    volatile int _k = state->k; (void)_k;\n",
        text,
        count=1,
    )
    assert hits == 1
    head.write_text(text, encoding="utf-8")
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=proj,
        env={"PYTHONPATH": str(proj / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert run.stdout.splitlines() == [
        "gain: RuntimeError destroyed",
        "set_k: RuntimeError destroyed",
        "step: RuntimeError destroyed",
        "set_taps: RuntimeError destroyed",
    ], run.stdout
