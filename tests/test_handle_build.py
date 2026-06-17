"""End-to-end BUILD test for ``kind = "handle"`` modules (gh-306).

Unlike ``test_handle_apply.py`` (which asserts on generated *text*), this
scaffolds a project, runs ``jm apply``, then **compiles the generated binding
together with a real C backing and imports the resulting ``.so``**, exercising
the typed class for real: the constructor, an array-in method, a mixed
array+scalar method (#308), an int-in→array-out method, the decoded-getter and
derived-``expr`` properties, and the context-manager / closed guard.

This is the harness that caught the doubled-brace codegen bug a text-assertion
missed — the first *real compile* of handle output, run in CI against the test
interpreter's own Python/numpy ABI. Skipped only where no C compiler is
available."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._new import run as new_run

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
pytestmark = pytest.mark.skipif(_CC is None, reason="no C compiler available")


# A real FIFO ring buffer — the hand-C backing the generated glue wraps. The
# `push_gain` method is the #308 array+scalar shape; `stats` fills a struct
# (a live getter); `info` fills the fixed-metadata struct a cache=true getter
# resolves once at construction.
_RINGBUF_H = """\
#ifndef RINGBUF_H
#define RINGBUF_H
#include <stddef.h>
typedef struct ringbuf ringbuf_t;
typedef struct { size_t used; } ringbuf_stats_t;
typedef struct { size_t capacity; } ringbuf_info_t;
ringbuf_t *ringbuf_open(size_t capacity);
void ringbuf_close(ringbuf_t *r);
size_t ringbuf_push(ringbuf_t *r, const float *x, size_t n);
size_t ringbuf_push_gain(ringbuf_t *r, const float *x, size_t n, float gain);
size_t ringbuf_pop(ringbuf_t *r, float *out, size_t n);
void ringbuf_clear(ringbuf_t *r);
void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out);
void ringbuf_info(const ringbuf_t *r, ringbuf_info_t *out);
#endif
"""

_RINGBUF_C = """\
#include "ringbuf/ringbuf.h"
#include <stdlib.h>
struct ringbuf { float *buf; size_t cap, used, head; };
ringbuf_t *ringbuf_open(size_t capacity) {
    if (!capacity) return NULL;
    ringbuf_t *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    r->buf = malloc(capacity * sizeof *r->buf);
    if (!r->buf) { free(r); return NULL; }
    r->cap = capacity;
    return r;
}
void ringbuf_close(ringbuf_t *r) { if (r) { free(r->buf); free(r); } }
size_t ringbuf_push(ringbuf_t *r, const float *x, size_t n) {
    size_t k = 0;
    for (; k < n && r->used < r->cap; k++) {
        r->buf[(r->head + r->used) % r->cap] = x[k];
        r->used++;
    }
    return k;
}
size_t ringbuf_push_gain(ringbuf_t *r, const float *x, size_t n, float gain) {
    size_t k = 0;
    for (; k < n && r->used < r->cap; k++) {
        r->buf[(r->head + r->used) % r->cap] = x[k] * gain;
        r->used++;
    }
    return k;
}
size_t ringbuf_pop(ringbuf_t *r, float *out, size_t n) {
    size_t g = 0;
    for (; g < n && r->used > 0; g++) {
        out[g] = r->buf[r->head];
        r->head = (r->head + 1) % r->cap;
        r->used--;
    }
    return g;
}
void ringbuf_clear(ringbuf_t *r) { r->used = 0; r->head = 0; }
void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out) {
    out->used = r->used;
}
void ringbuf_info(const ringbuf_t *r, ringbuf_info_t *out) {
    out->capacity = r->cap;
}
"""


def _ringbuf_module() -> dict:
    return {
        "kind": "handle",
        "backing": "ringbuf",
        "header": "ringbuf/ringbuf.h",
        "type_name": "Ring",
        "context_manager": True,
        "create_fn": "ringbuf_open",
        "close_fn": "ringbuf_close",
        "create_args": [{"name": "capacity", "type": "size_t"}],
        "methods": [
            {
                "name": "push",
                "fn": "ringbuf_push",
                "returns": "size_t",
                "args": [{"name": "x", "type": "float[]"}],
            },
            {
                "name": "push_gain",  # #308: array + trailing scalar
                "fn": "ringbuf_push_gain",
                "returns": "size_t",
                "args": [
                    {"name": "x", "type": "float[]"},
                    {"name": "gain", "type": "float"},
                ],
            },
            {
                "name": "pop",
                "fn": "ringbuf_pop",
                "returns": "float[]",
                "args": [{"name": "n", "type": "size_t"}],
            },
            {"name": "clear", "fn": "ringbuf_clear"},
        ],
        "getters": [
            {
                "fn": "ringbuf_stats",
                "out": "ringbuf_stats_t",
                "cache": False,
                "fields": [
                    {"name": "used", "from": "used", "type": "size_t"},
                    {
                        "name": "fill_fraction",
                        "type": "double",
                        "expr": "self->capacity ? (double)tmp.used / "
                        "(double)self->capacity : 0.0",
                    },
                ],
            },
            {
                # cache=true: fixed metadata resolved ONCE in tp_init. If the
                # cache fetch is not wired into the constructor the struct stays
                # zero-initialized and `cap` reads 0 (the gh-306 regression).
                "fn": "ringbuf_info",
                "out": "ringbuf_info_t",
                "cache": True,
                "fields": [
                    {"name": "cap", "from": "capacity", "type": "size_t"},
                ],
            },
        ],
    }


def _build_ring_so(tmp: Path):
    """Scaffold → apply → compile the generated ringbuf .so; import + return it."""
    new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg.setdefault("module", {})["ringbuf"] = _ringbuf_module()
    C.save(tmp, cfg)
    apply_run(tmp)

    inc = tmp / "native" / "inc" / "ringbuf"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "ringbuf.h").write_text(_RINGBUF_H)
    (tmp / "native" / "src" / "ringbuf" / "ringbuf.c").write_text(_RINGBUF_C)

    ext_c = tmp / "native" / "src" / "ringbuf" / "ringbuf_ext.c"
    backing_c = tmp / "native" / "src" / "ringbuf" / "ringbuf.c"
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    so = tmp / f"ringbuf{suffix}"

    link = (
        ["-bundle", "-undefined", "dynamic_lookup"]
        if sys.platform == "darwin"
        else ["-shared"]
    )
    cmd = [
        _CC,
        *link,
        "-fPIC",
        "-O2",
        "-std=c11",
        "-I",
        str(tmp / "native" / "inc"),
        "-I",
        sysconfig.get_path("include"),
        "-I",
        np.get_include(),
        str(ext_c),
        str(backing_c),
        "-o",
        str(so),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    # The init symbol is PyInit_<final name component>, so the spec name must
    # match the generated PyInit_ringbuf (not an arbitrary alias).
    spec = importlib.util.spec_from_file_location("ringbuf", so)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_handle_so_compiles_imports_and_runs(tmp_path):
    mod = _build_ring_so(tmp_path)
    Ring = mod.Ring

    r = Ring(capacity=4)
    assert r.used == 0 and r.fill_fraction == 0.0
    # cache=true getter resolved once in tp_init (regression guard: a missing
    # cache fetch leaves the struct zeroed and this reads 0, not 4).
    assert r.cap == 4

    # array-in -> scalar (drops when full at capacity 4)
    assert r.push(np.array([1, 2, 3, 4, 5, 6], dtype=np.float32)) == 4
    assert r.used == 4 and r.fill_fraction == 1.0  # derived expr

    # int-in -> independent array-out, FIFO
    assert r.pop(2).tolist() == [1.0, 2.0]
    assert r.used == 2 and r.fill_fraction == 0.5

    r.clear()
    assert r.used == 0


def test_mixed_array_scalar_method_passes_scalars(tmp_path):
    """#308: push_gain(x, gain) must apply the scalar, not drop it."""
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=8)
    assert r.push_gain(np.array([1, 2, 3], dtype=np.float32), 10.0) == 3
    assert r.pop(3).tolist() == [10.0, 20.0, 30.0]


def test_cache_true_getter_resolved_in_tp_init(tmp_path):
    """gh-306 regression: a cache=true getter is resolved once in tp_init.

    Before the fix the cache fetch was never emitted into the constructor, so
    the stashed out-struct stayed zero-initialized and the cached property read
    0. Here `cap` must equal the constructor capacity for every instance."""
    Ring = _build_ring_so(tmp_path).Ring
    assert Ring(capacity=4).cap == 4
    assert Ring(capacity=16).cap == 16


def test_context_manager_and_closed_guard(tmp_path):
    Ring = _build_ring_so(tmp_path).Ring
    with Ring(capacity=8) as r:
        r.push(np.arange(3, dtype=np.float32))
        assert r.used == 3
    # after __exit__ the handle is closed; property access raises
    with pytest.raises(RuntimeError):
        _ = r.used
