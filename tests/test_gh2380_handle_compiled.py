"""gh-2380: a handle's methods and property, compiled and run.

The scan (`test_gh2380_order_scan_handle`) reads the generated C. This is the
truth anchor behind it: the backing allocates its state with ``mmap`` and
``munmap``s it on close, so a call that reaches the backing after ``close()``
faults deterministically (SIGSEGV, not an allocator-dependent read). An
argument whose conversion runs Python (``__array__`` / ``__float__``) closes
the handle; the call must then raise ``RuntimeError``, not die.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from test_handle_build import _compile_import, _placed  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

H = """\
#ifndef SLOW_H
#define SLOW_H
#include <stddef.h>
typedef struct slow slow_t;
slow_t *slow_open(int k);
void slow_close(slow_t *s);
int slow_feed(slow_t *s, const float *x, size_t n);
size_t slow_scale(slow_t *s, const float *x, size_t n, float *out,
                  size_t max_out);
float slow_get_gain(const slow_t *s);
void slow_set_gain(slow_t *s, float v);
#endif
"""

CSRC = """\
#include <sys/mman.h>
#include "slow/slow.h"
struct slow { int k; float gain; };
slow_t *slow_open(int k) {
    void *p = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return NULL;
    slow_t *s = p;
    s->k = k;
    return s;
}
void slow_close(slow_t *s) { munmap(s, 4096); }
int slow_feed(slow_t *s, const float *x, size_t n) {
    (void)x;
    return s->k + (int)n;     /* reads the state: faults if unmapped */
}
size_t slow_scale(slow_t *s, const float *x, size_t n, float *out,
                  size_t max_out) {
    size_t m = n < max_out ? n : max_out;
    for (size_t i = 0; i < m; i++) out[i] = x[i] * (float)s->k;
    return m;
}
float slow_get_gain(const slow_t *s) { return s->gain; }
void slow_set_gain(slow_t *s, float v) { s->gain = v; }   /* writes it */
"""

PROBE = """\
import numpy as np
import slow

out = []


def attempt(label, call):
    h = slow.Slow(1)
    try:
        call(h)
        out.append(label + ": returned")
    except RuntimeError as e:
        out.append(label + ": RuntimeError " + str(e))


class Arr:
    def __init__(self, h):
        self.h = h

    def __array__(self, dtype=None, copy=None):
        self.h.close()
        return np.ones(3, dtype=np.float32)


class Flt:
    def __init__(self, h):
        self.h = h

    def __float__(self):
        self.h.close()
        return 2.5


attempt("feed", lambda h: h.feed(Arr(h)))
attempt("scale", lambda h: h.scale(Arr(h), np.zeros(3, np.float32)))
attempt("gain", lambda h: setattr(h, "gain", Flt(h)))
print("\\n".join(out))
"""


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_close_in_conversion_is_an_error_not_a_crash(tmp_path):
    new_run("proj", tmp_path, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp_path)
    cfg.setdefault("module", {})["slow"] = _placed(
        {
            "kind": "handle",
            "backing": "slow",
            "header": "slow/slow.h",
            "type_name": "Slow",
            "create_fn": "slow_open",
            "close_fn": "slow_close",
            "create_args": [{"name": "k", "type": "int"}],
            "methods": [
                {
                    "name": "feed",
                    "fn": "slow_feed",
                    "args": [{"name": "x", "type": "float[]"}],
                    "returns": "int",
                },
                {
                    "name": "scale",
                    "fn": "slow_scale",
                    "returns": "float[]",
                    "args": [
                        {"name": "x", "type": "float[]"},
                        {"name": "out", "type": "float[]", "writable": True},
                    ],
                },
            ],
            "getters": [
                {
                    "fn": "slow_get_gain",
                    "out": "float",
                    "fields": [
                        {
                            "name": "gain",
                            "type": "float",
                            "writable_fn": "slow_set_gain",
                        }
                    ],
                }
            ],
        },
        tmp_path,
    )
    C.save(tmp_path, cfg)
    apply_run(tmp_path)
    _compile_import(tmp_path, "slow", H, CSRC)
    (tmp_path / "probe.py").write_text(PROBE, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, "probe.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert run.stdout.splitlines() == [
        "feed: RuntimeError Slow is closed",
        "scale: RuntimeError Slow is closed",
        "gain: RuntimeError Slow is closed",
    ], run.stdout
