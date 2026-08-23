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
# resolves once at construction; `scale` is the #311 array-in→writable-array-out
# execute shape; `gain` is the #311 writable scalar property.
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
size_t ringbuf_scale(const ringbuf_t *r, const float *in, size_t n_in,
                     float *out, size_t max_out);
size_t ringbuf_scale_ctrl(const ringbuf_t *r, const float *in, size_t n_in,
                          float extra, float bias,
                          float *out, size_t max_out);
float ringbuf_get_gain(const ringbuf_t *r);
void ringbuf_set_gain(ringbuf_t *r, float gain);
size_t ringbuf_head(const ringbuf_t *r);
size_t ringbuf_save_bytes(const ringbuf_t *r);
size_t ringbuf_save(const ringbuf_t *r, void *out);
ringbuf_t *ringbuf_restore(const void *blob, size_t n);
int ringbuf_dump(const ringbuf_t *r, const char *path);
ringbuf_t *ringbuf_load(const char *path);
#endif
"""

_RINGBUF_C = """\
#include "ringbuf/ringbuf.h"
#include <stdlib.h>
#include <stdio.h>
struct ringbuf { float *buf; size_t cap, used, head; float gain; };
ringbuf_t *ringbuf_open(size_t capacity) {
    if (!capacity) return NULL;
    ringbuf_t *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    r->buf = malloc(capacity * sizeof *r->buf);
    if (!r->buf) { free(r); return NULL; }
    r->cap = capacity;
    r->gain = 1.0f;
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
size_t ringbuf_scale(const ringbuf_t *r, const float *in, size_t n_in,
                     float *out, size_t max_out) {
    size_t k = 0;
    for (; k < n_in && k < max_out; k++) out[k] = in[k] * r->gain;
    return k;
}
/* gh-582: the block form of ringbuf_scale with two control ports — the
   scalars sit between n_in and the output buffer, which is the natural C
   signature for a streaming block plus loop controls. */
size_t ringbuf_scale_ctrl(const ringbuf_t *r, const float *in, size_t n_in,
                          float extra, float bias,
                          float *out, size_t max_out) {
    size_t k = 0;
    for (; k < n_in && k < max_out; k++)
        out[k] = in[k] * r->gain * extra + bias;
    return k;
}
float ringbuf_get_gain(const ringbuf_t *r) { return r->gain; }
void ringbuf_set_gain(ringbuf_t *r, float gain) { r->gain = gain; }
size_t ringbuf_head(const ringbuf_t *r) { return r->head; }
size_t ringbuf_save_bytes(const ringbuf_t *r) {
    return r->used * sizeof(float);
}
size_t ringbuf_save(const ringbuf_t *r, void *out) {
    char *dst = (char *)out;
    for (size_t i = 0; i < r->used; i++) {
        float v = r->buf[(r->head + i) % r->cap];
        __builtin_memcpy(dst + i * sizeof(float), &v, sizeof(float));
    }
    return r->used * sizeof(float);
}
ringbuf_t *ringbuf_restore(const void *blob, size_t n) {
    size_t count = n / sizeof(float);
    ringbuf_t *r = ringbuf_open(count ? count : 1);
    if (!r) return NULL;
    ringbuf_push(r, (const float *)blob, count);
    return r;
}
int ringbuf_dump(const ringbuf_t *r, const char *path) {
    FILE *fp = fopen(path, "wb");
    if (!fp) return 1;                       /* e.g. bad directory */
    for (size_t i = 0; i < r->used; i++) {
        float v = r->buf[(r->head + i) % r->cap];
        if (fwrite(&v, sizeof(float), 1, fp) != 1) { fclose(fp); return 2; }
    }
    return fclose(fp) != 0 ? 3 : 0;          /* short write surfaces here */
}
ringbuf_t *ringbuf_load(const char *path) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return NULL;
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    size_t count = (sz > 0) ? (size_t)sz / sizeof(float) : 0;
    ringbuf_t *r = ringbuf_open(count ? count : 1);
    if (!r) { fclose(fp); return NULL; }
    if (count) { size_t g = fread(r->buf, sizeof(float), count, fp); (void)g; }
    r->used = count;
    fclose(fp);
    return r;
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
        # gh-565: module-level alternate constructors over a blob and a file.
        "factories": [
            {
                "name": "RingFromBlob",
                "create_fn": "ringbuf_restore",
                "init_params": [{"name": "blob", "type": "bytes"}],
            },
            {
                "name": "RingFromFile",
                "create_fn": "ringbuf_load",
                "init_params": [{"name": "path", "type": "path"}],
            },
        ],
        "methods": [
            {
                # gh-565: a path method arg + an int->raise status (error).
                # gh-1111: ...carrying the author's own `error_message`, which
                # this face accepted and dropped. The `%` is deliberate: the
                # binding used to splice its subject into the PyErr_Format
                # FORMAT string, where a `%` in ordinary prose becomes a live
                # conversion with no vararg behind it. This is the only gate
                # that COMPILES and RUNS that path.
                "name": "dump",
                "fn": "ringbuf_dump",
                "returns": "int",
                "error": "OSError",
                "error_message": "the dump did not reach 100% of the buffer",
                "args": [{"name": "path", "type": "path"}],
            },
            {
                "name": "push",
                "fn": "ringbuf_push",
                "returns": "size_t",
                "args": [{"name": "x", "type": "float[]"}],
            },
            {
                # gh-565 shape (f): handle-length `bytes` return.
                "name": "save",
                "fn": "ringbuf_save",
                "out_len_fn": "ringbuf_save_bytes",
                "returns": "bytes",
            },
            {
                "name": "push_gain",  # #308: array + trailing scalar
                "fn": "ringbuf_push_gain",
                "returns": "size_t",
                "args": [
                    {"name": "x", "type": "float[]"},
                    # gh-178 review #6: a trailing-scalar default, callable
                    # positionally, by keyword, or omitted.
                    {"name": "gain", "type": "float", "default": "1.0f"},
                ],
            },
            {
                "name": "pop",
                "fn": "ringbuf_pop",
                "returns": "float[]",
                "args": [{"name": "n", "type": "size_t"}],
            },
            {
                # #311 shape (d): array-in + writable array-out -> out[:n_out]
                "name": "scale",
                "fn": "ringbuf_scale",
                "returns": "float[]",
                "nogil": True,
                "args": [
                    {"name": "x", "type": "float[]"},
                    {"name": "out", "type": "float[]", "writable": True},
                ],
            },
            {
                # gh-582 shape (d) + trailing scalars: the control-port variant
                # of `scale`. `bias` carries a default, which is what forces the
                # `|` into the format string and makes the explicit
                # required-`out` check load-bearing.
                "name": "scale_ctrl",
                "fn": "ringbuf_scale_ctrl",
                "returns": "float[]",
                "nogil": True,
                "args": [
                    {"name": "x", "type": "float[]"},
                    {"name": "extra", "type": "float"},
                    {"name": "bias", "type": "float", "default": "0.0f"},
                    {"name": "out", "type": "float[]", "writable": True},
                ],
            },
            {
                # #319: a method with a default scalar arg, callable
                # positionally, by keyword, or omitted (reuses ringbuf_set_gain).
                "name": "reset_gain",
                "fn": "ringbuf_set_gain",
                "args": [{"name": "to", "type": "float", "default": "1.0f"}],
            },
            {"name": "clear", "fn": "ringbuf_clear"},
        ],
        "getters": [
            {
                # #314: per-field scalar getter — no shared struct/shim.
                "fields": [
                    {
                        "name": "head_pos",
                        "getter": "ringbuf_head",
                        "type": "size_t",
                    },
                    {
                        # gh-326: a bool derived from a float getter. `tmp` must
                        # be the getter's return type (float), not the field type
                        # (bool) — else gain truncates to 0/1 and `tmp > 1.0` is
                        # always false. Also needs <stdbool.h> to compile.
                        "name": "loud",
                        "getter": "ringbuf_get_gain",
                        "type": "bool",
                        "returns": "float",
                        "expr": "tmp > 1.0",
                    },
                ],
            },
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
            {
                # #311 writable scalar property: a scalar (return-by-value)
                # getter whose field names a setter -> a read/write `gain`.
                "fn": "ringbuf_get_gain",
                "out": "float",
                "fields": [
                    {
                        "name": "gain",
                        "type": "float",
                        "writable_fn": "ringbuf_set_gain",
                    },
                ],
            },
        ],
    }


def _compile_import(
    tmp: Path, mod_name: str, header_src: str, backing_src: str
):
    """Compile a materialized handle module's binding + its C backing into a
    ``.so`` and import it. The init symbol is ``PyInit_<mod_name>``, so the spec
    name must match (not an arbitrary alias)."""
    inc = tmp / "native" / "inc" / mod_name
    inc.mkdir(parents=True, exist_ok=True)
    (inc / f"{mod_name}.h").write_text(header_src)
    backing_c = tmp / "native" / "src" / mod_name / f"{mod_name}.c"
    backing_c.write_text(backing_src)
    ext_c = tmp / "native" / "src" / mod_name / f"{mod_name}_ext.c"
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    so = tmp / f"{mod_name}{suffix}"

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
    subprocess.run(
        cmd, check=True, capture_output=True, text=True, timeout=600
    )

    spec = importlib.util.spec_from_file_location(mod_name, so)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_ring_so(tmp: Path):
    """Scaffold → apply → compile the generated ringbuf .so; import + return it."""
    new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg.setdefault("module", {})["ringbuf"] = _ringbuf_module()
    C.save(tmp, cfg)
    apply_run(tmp)
    return _compile_import(tmp, "ringbuf", _RINGBUF_H, _RINGBUF_C)


# A second backing: an init-in-place struct (#315) with no heap members — jm
# mallocs sizeof(ticks_t), calls ticks_init, and free()s on close (no destroy
# fn needed). `value` is a #314 per-field getter; `bump` a no-arg method.
_TICKS_H = """\
#ifndef TICKS_H
#define TICKS_H
typedef struct { int n; } ticks_t;
void ticks_init(ticks_t *t, int start);
void ticks_bump(ticks_t *t);
int ticks_value(const ticks_t *t);
#endif
"""

_TICKS_C = """\
#include "ticks/ticks.h"
void ticks_init(ticks_t *t, int start) { t->n = start; }
void ticks_bump(ticks_t *t) { t->n++; }
int ticks_value(const ticks_t *t) { return t->n; }
"""


def _ticks_module() -> dict:
    return {
        "kind": "handle",
        "type_name": "Ticks",
        "handle_type": "ticks_t",
        "header": "ticks/ticks.h",
        "init_fn": "ticks_init",  # #315: init-in-place; jm mallocs + frees
        "create_args": [{"name": "start", "type": "int", "default": "0"}],
        "methods": [{"name": "bump", "fn": "ticks_bump"}],
        "getters": [
            {
                "fields": [
                    {"name": "value", "getter": "ticks_value", "type": "int"}
                ]
            }
        ],
    }


def _build_ticks_so(tmp: Path):
    new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg.setdefault("module", {})["ticks"] = _ticks_module()
    C.save(tmp, cfg)
    apply_run(tmp)
    return _compile_import(tmp, "ticks", _TICKS_H, _TICKS_C)


# A third backing whose close_fn reports a status code (gh-178 review #5).
# `flaky_close` returns the code stashed at open; close() must raise on nonzero.
_FLAKY_H = """\
#ifndef FLAKY_H
#define FLAKY_H
typedef struct flaky flaky_t;
flaky_t *flaky_open(int rc);
int flaky_close(flaky_t *f);
#endif
"""

_FLAKY_C = """\
#include "flaky/flaky.h"
#include <stdlib.h>
struct flaky { int rc; };
flaky_t *flaky_open(int rc) {
    flaky_t *f = malloc(sizeof *f);
    if (f) f->rc = rc;
    return f;
}
int flaky_close(flaky_t *f) { int rc = f->rc; free(f); return rc; }
"""


def _flaky_module() -> dict:
    return {
        "kind": "handle",
        "backing": "flaky",
        "header": "flaky/flaky.h",
        "type_name": "Flaky",
        "context_manager": True,
        "create_fn": "flaky_open",
        "close_fn": "flaky_close",
        "close_returns": "int",  # gh-178 review #5
        "create_args": [{"name": "rc", "type": "int", "default": "0"}],
        "methods": [],
        "getters": [],
    }


def _build_flaky_so(tmp: Path):
    new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(tmp)
    cfg.setdefault("module", {})["flaky"] = _flaky_module()
    C.save(tmp, cfg)
    apply_run(tmp)
    return _compile_import(tmp, "flaky", _FLAKY_H, _FLAKY_C)


def test_close_status_code_raises(tmp_path):
    """gh-178 review #5: close() captures close_fn's rc and raises on nonzero;
    a zero rc closes cleanly. The handle is marked closed either way (one-shot),
    so a second close is a silent no-op and there is no double-free."""
    Flaky = _build_flaky_so(tmp_path).Flaky

    Flaky(rc=0).close()  # clean close, no raise

    f = Flaky(rc=7)
    with pytest.raises(RuntimeError):
        f.close()
    f.close()  # already closed -> no-op, no double-free

    # __exit__ propagates the close failure too.
    with pytest.raises(RuntimeError):
        with Flaky(rc=1):
            pass


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


def test_bytes_save_roundtrip(tmp_path):
    """gh-565 shape (f): save() returns handle-length `bytes`, sized by
    out_len_fn, packed by fn, COPIED into an immutable bytes object."""
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=8)

    # Empty handle -> zero-length blob (out_len_fn returns 0; the buffer is
    # still allocated with a floor of 1 so PyMem_Malloc never sees 0).
    assert r.save() == b""

    xs = np.array([1.5, -2.0, 3.25], dtype=np.float32)
    assert r.push(xs) == 3
    blob = r.save()
    assert isinstance(blob, bytes)
    # The backing serializes the live float ring in FIFO order; decode and
    # compare to prove the (const void*, size_t) marshaling is bit-exact.
    assert np.frombuffer(blob, dtype=np.float32).tolist() == xs.tolist()
    # save() does not consume the ring (it is a read-only snapshot).
    assert r.used == 3
    assert r.save() == blob


def test_bytes_factory_restore_roundtrip(tmp_path):
    """gh-565 slice 3: a module-level factory (alternate constructor) rebuilds a
    fresh handle from a blob. RingFromBlob(r.save()) reconstructs the ring, and
    the reconstructed instance IS the module's typed class (usable as one)."""
    mod = _build_ring_so(tmp_path)
    Ring, RingFromBlob = mod.Ring, mod.RingFromBlob

    r = Ring(capacity=8)
    r.push(np.array([1.5, -2.0, 3.25, 4.0], dtype=np.float32))
    blob = r.save()

    # Rebuild a brand-new Ring straight from the bytes — no primary ctor.
    r2 = RingFromBlob(blob)
    assert isinstance(r2, Ring)
    # It carries the same contents: its own save() round-trips identically.
    assert r2.save() == blob
    assert np.frombuffer(r2.save(), dtype=np.float32).tolist() == [
        1.5,
        -2.0,
        3.25,
        4.0,
    ]
    # It is an independent handle: draining r2 does not touch r.
    assert r2.pop(4).tolist() == [1.5, -2.0, 3.25, 4.0]
    assert r.used == 4
    # RAII still works on a factory-built instance.
    r2.close()


def test_dump_and_file_factory_roundtrip(tmp_path):
    """gh-565: dump(path) writes via C (no I/O in Python), returns None on
    success and raises the declared OSError on a non-zero rc; PlanFromFile-style
    path factory reads it back."""
    mod = _build_ring_so(tmp_path)
    Ring, RingFromFile = mod.Ring, mod.RingFromFile

    r = Ring(capacity=8)
    r.push(np.array([1.0, 2.5, -3.0], dtype=np.float32))
    p = tmp_path / "ring.bin"

    # Success: the C write path returns 0 -> the binding returns None.
    assert r.dump(str(p)) is None
    assert p.exists()

    # The path factory reconstructs from the file.
    r2 = RingFromFile(str(p))
    assert isinstance(r2, Ring)
    assert r2.pop(3).tolist() == [1.0, 2.5, -3.0]

    # A non-zero rc (unwritable path) raises the declared OSError -- with the
    # declared message and the rc appended (gh-1111), not a canned
    # "<fn> failed". The `%` survives verbatim because the message crosses as
    # a PyErr_Format ARGUMENT rather than as its format string.
    with pytest.raises(OSError) as excinfo:
        r.dump(str(tmp_path / "no-such-dir" / "ring.bin"))
    assert "the dump did not reach 100% of the buffer" in str(excinfo.value)
    assert "(rc=1)" in str(excinfo.value)


def test_mixed_array_scalar_method_passes_scalars(tmp_path):
    """#308: push_gain(x, gain) must apply the scalar, not drop it. gh-178
    review #6: the trailing scalar carries a default and accepts keywords."""
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=8)
    assert r.push_gain(np.array([1, 2, 3], dtype=np.float32), 10.0) == 3
    assert r.pop(3).tolist() == [10.0, 20.0, 30.0]

    # keyword form
    assert r.push_gain(np.array([1, 2], dtype=np.float32), gain=4.0) == 2
    assert r.pop(2).tolist() == [4.0, 8.0]

    # omitted -> default gain 1.0
    assert r.push_gain(np.array([5, 6], dtype=np.float32)) == 2
    assert r.pop(2).tolist() == [5.0, 6.0]


def test_required_create_arg_rejects_missing(tmp_path):
    """gh-178 review #6: a no-default create-arg (capacity) is REQUIRED — the
    old all-optional `|` let `Ring()` build with a 0 capacity (NULL handle)."""
    Ring = _build_ring_so(tmp_path).Ring
    with pytest.raises(TypeError):
        Ring()  # missing required capacity
    assert Ring(4).cap == 4  # positional required arg still works


def test_reinit_releases_prior_handle(tmp_path):
    """gh-178 review #9: a second __init__() on a live object releases the old
    handle and rebuilds — no double-free, no crash, fresh state."""
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=4)
    r.push(np.array([1, 2, 3], dtype=np.float32))
    assert r.used == 3

    # re-init: old handle torn down, new one built. State is fresh, the object
    # still works (a leaked/double-freed handle would crash here).
    r.__init__(capacity=8)
    assert r.used == 0 and r.cap == 8
    assert r.push(np.arange(5, dtype=np.float32)) == 5
    assert r.used == 5


def test_writable_property_and_execute_shape(tmp_path):
    """#311: a writable scalar property + the array-in→writable-array-out shape.

    `gain` is a read/write property (scalar return-by-value getter + setter);
    `scale(x, out)` marshals a borrowed input and a writable exact-dtype output,
    writes in place, and returns the zero-copy view `out[:n_out]`."""
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=8)

    # writable scalar property: default, then set -> get round-trip.
    assert r.gain == 1.0
    r.gain = 3.0
    assert r.gain == 3.0

    # shape (d): execute into the caller's buffer; returns out[:n_out] view.
    out = np.zeros(4, dtype=np.float32)
    y = r.scale(np.array([1, 2, 3, 4], dtype=np.float32), out)
    assert y.tolist() == [3.0, 6.0, 9.0, 12.0]
    assert out.tolist() == [3.0, 6.0, 9.0, 12.0]  # written in place
    assert y.base is out  # zero-copy view, not a copy

    # exact-dtype enforcement: a wrong-dtype out is rejected, not silently cast.
    with pytest.raises(TypeError):
        r.scale(
            np.array([1, 2], dtype=np.float32),
            np.zeros(2, dtype=np.float64),
        )


def test_execute_shape_with_trailing_scalars(tmp_path):
    """gh-582: shape (d) carrying control scalars, like shape (b) already could.

    `scale_ctrl(x, extra, bias=0.0, out)` threads its scalars between `n_in` and
    the output buffer — `fn(h, in, n_in, extra, bias, out, max_out)` — which is
    the natural C signature for a streaming block with control ports.
    """
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=8)
    r.gain = 2.0
    x = np.array([1, 2, 3, 4], dtype=np.float32)

    # positional, default bias: 1*2*3 = 6, 2*2*3 = 12, ...
    out = np.zeros(4, dtype=np.float32)
    y = r.scale_ctrl(x, out, 3.0)
    assert y.tolist() == [6.0, 12.0, 18.0, 24.0]
    assert out.tolist() == [6.0, 12.0, 18.0, 24.0]  # caller's buffer written
    assert y.base is out  # still the zero-copy view

    # the default is a real default, and the scalars are keyword-capable
    out2 = np.zeros(4, dtype=np.float32)
    y2 = r.scale_ctrl(x, out2, extra=3.0, bias=1.0)
    assert y2.tolist() == [7.0, 13.0, 19.0, 25.0]

    # `out` precedes the scalars, so it stays REQUIRED even though `bias`
    # carries a default: PyArg's `|` makes everything after it optional, and a
    # required parameter cannot follow an optional one positionally.
    with pytest.raises(TypeError):
        r.scale_ctrl(x)

    # the exact-dtype guard still applies on this path
    with pytest.raises(TypeError):
        r.scale_ctrl(x, np.zeros(4, dtype=np.float64), 3.0)

    # a short output buffer truncates to max_out, as with bare scale()
    short = np.zeros(2, dtype=np.float32)
    assert r.scale_ctrl(x, short, 1.0).tolist() == [2.0, 4.0]


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


def test_per_field_getter_and_method_default(tmp_path):
    """#314 per-field scalar getter (`head_pos` via its own getter) + #319 a
    method honoring a default / keyword arg (`reset_gain`)."""
    Ring = _build_ring_so(tmp_path).Ring
    r = Ring(capacity=4)

    # #314: head_pos reads ringbuf_head directly (no struct shim).
    assert r.head_pos == 0
    r.push(np.array([1, 2, 3], dtype=np.float32))
    r.pop(2)
    assert r.head_pos == 2  # head advanced by the 2 pops

    # gh-326: `loud` is a bool derived from the float gain getter. With `tmp`
    # mis-typed as bool the float truncates and this is always False.
    r.gain = 3.0
    assert r.loud is True
    r.gain = 0.5
    assert r.loud is False

    # #319: reset_gain(to=...) — positional, keyword, and omitted (default).
    r.gain = 9.0
    r.reset_gain()  # default to=1.0
    assert r.gain == 1.0
    r.reset_gain(to=2.5)  # keyword
    assert r.gain == 2.5
    r.reset_gain(4.0)  # positional still works
    assert r.gain == 4.0


def test_init_in_place_handle(tmp_path):
    """#315: an init_fn handle — jm mallocs the struct, runs init_fn, frees on
    close. (Also exercises a no-arg method and a #314 per-field getter.)"""
    Ticks = _build_ticks_so(tmp_path).Ticks

    t = Ticks(start=5)
    assert t.value == 5  # init_fn ran over the jm-malloc'd struct
    t.bump()
    t.bump()
    assert t.value == 7

    assert Ticks().value == 0  # default start=0

    # close frees the struct; the closed guard then fires (no use-after-free).
    t.close()
    with pytest.raises(RuntimeError):
        _ = t.value
