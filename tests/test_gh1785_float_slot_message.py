"""A status message naming a float slot raises what it declares (gh-1785).

gh-1426 C let a ``status_errors`` message name a param or a property, and
gh-1614 gave a ``check_return`` function the same table through the same
emitter (`_diagnostics.format_raise_c`). A float slot was given ``%g``, and
``PyErr_Format`` formats through ``PyUnicode_FromFormatV``, which has no
floating-point conversion: the row compiled, and when it fired the caller
got ``SystemError: invalid format string: %g is out of range`` instead of
the declared exception. Nothing caught it because no test FIRED a float row
-- they read the rendered text, and the text looked right.

So these tests build a real project and fire a float row on every face the
emitter serves: a borrow's float PARAM and float PROPERTY (gh-1426 C), a
function's float param (gh-1614), and the same under ``why = true``, where
the raise is the ``else`` of `_diagnostics.reason_raise_c` (gh-1706). Each
asserts the declared class and the value as Python's ``repr`` writes it.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit._context import _diagnostics as D  # noqa: E402
from test_gh1614_function_status_errors import (  # noqa: E402
    _SKIP,
    _build,
    _implement,
    _project,
)


def test_a_conversion_pyerr_format_lacks_is_refused_at_render():
    """The render-time half: CPython checks the format only when the raise
    fires, so a printf-only conversion must not survive to the C."""
    with pytest.raises(RuntimeError, match="does not accept"):
        D.format_raise_c("ValueError", "g={g}", {"g": ("%g", "(double)g")})


#: The object face: a borrow whose status row names a float param (`thr`, a
#: C `float`, so the widening cast is on the path too) and a double property.
_BORROW_ROW = (
    "P_TOO_LARGE:ValueError:"
    "wait({n}) at thr {thr} exceeds gain {gain}, 100% full"
)

_FUNCTIONS = """
[[module.m.functions]]
name = "scale"
return_type = "int"
check_return = true
status_errors = [
  {status = "P_ERR_RANGE", error = "ValueError", message = "gain {g} is out of range"},
]

[[module.m.functions.params]]
name = "g"
type = "double"

[[module.m.functions]]
name = "trim"
return_type = "int"
check_return = true
why = true
status_errors = [
  {status = "P_ERR_RANGE", error = "OverflowError", message = "level {g} vs {g}"},
]

[[module.m.functions.params]]
name = "g"
type = "double"
"""

_CODES = "enum { P_ERR_RANGE = -3, P_TOO_LARGE = 7 };\n"

_SCALE_BODY = """
    return g > 1.0 ? P_ERR_RANGE : 0;"""

#: `why` is declared and never written: the row's own message is the else
#: branch, which is the one that interpolates.
_TRIM_BODY = """
    (void)why;
    return g > 1.0 ? P_ERR_RANGE : 0;"""

_CHECK = r"""
from p import m
from p import Ring

def raises(call, cls, text):
    try:
        call()
    except Exception as e:
        assert type(e) is cls and str(e) == text, repr(e)
    else:
        raise AssertionError("no raise")

assert m.scale(0.5) is None
raises(lambda: m.scale(1.1), ValueError, "gain 1.1 is out of range")
raises(lambda: m.trim(1e300), OverflowError, "level 1e+300 vs 1e+300")
raises(
    lambda: Ring(16).wait(4, 0.5),
    ValueError,
    "wait(4) at thr 0.5 exceeds gain 0.1, 100% full",
)

# Every rendered float is a PyMem allocation, which tracemalloc traces: a
# temporary left unfreed is a few bytes per slot per raise. Measured
# 2026-10-02: a clean run holds 0 bytes after 20000 raises over three
# temporaries, and one that drops the PyMem_Free holds ~190 KB.
import gc
import tracemalloc

ring = Ring(16)

def churn():
    for _ in range(10000):
        for call, cls in ((lambda: m.trim(1e300), OverflowError),
                          (lambda: ring.wait(4, 0.5), ValueError)):
            try:
                call()
            except cls:
                pass

churn()
gc.collect()
tracemalloc.start()
churn()
gc.collect()
current, _ = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert current < 16_000, f"{current} bytes still held after 20000 raises"
print("OK")
"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_a_float_slot_fires_its_declared_exception_end_to_end(tmp_path):
    from just_makeit._apply import run as apply_run

    dest = _project(tmp_path)
    for argv in (
        ("object", "ring", "--no-state", "--no-step", "--init-param",
         "n:size_t:16"),
        ("property", "ring", "gain", "--type", "double"),
        ("method", "ring", "wait", "--borrow", "--param", "n:size_t",
         "--param", "thr:float", "--return-type", "float _Complex",
         "--borrow-count", "n", "--status-fn", "p_ring_wait_status",
         "--status-error", _BORROW_ROW),
    ):  # fmt: skip
        r = run_cli(*argv, cwd=dest)
        assert r.returncode == 0, r.stdout + r.stderr

    manifest = dest / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _FUNCTIONS, encoding="utf-8"
    )
    anchor = '#include "p/clib_common.h"\n'
    for header in ("m/m_core.h", "ring/ring_core.h"):
        path = dest / "native/inc/p" / header
        text = path.read_text(encoding="utf-8")
        assert text.count(anchor) == 1, header
        path.write_text(text.replace(anchor, anchor + _CODES), "utf-8")
    ring_h = dest / "native/inc/p/ring/ring_core.h"
    text = ring_h.read_text(encoding="utf-8")
    create = "p_ring_state_t *p_ring_create(size_t n);\n"
    assert text.count(create) == 1
    ring_h.write_text(
        text.replace(
            create,
            create + "int p_ring_wait_status(p_ring_state_t *s, size_t n);\n",
        ),
        encoding="utf-8",
    )
    ring_c = dest / "native/src/ring/ring_core.c"
    text = ring_c.read_text(encoding="utf-8")
    stub = "    return 0.0; /* placeholder */"
    assert text.count(stub) == 1
    ring_c.write_text(
        text.replace(stub, "    (void)state;\n    return 0.1;")
        + "\nint\np_ring_wait_status(p_ring_state_t *s, size_t n)\n"
        "{\n    (void)s; (void)n;\n    return P_TOO_LARGE;\n}\n",
        encoding="utf-8",
    )

    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(dest)
    _implement(dest / "native/src/m/scale.c", _SCALE_BODY)
    _implement(dest / "native/src/m/trim.c", _TRIM_BODY)
    _build(dest)
    out = subprocess.run(
        [sys.executable, "-c", _CHECK],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "OK"
