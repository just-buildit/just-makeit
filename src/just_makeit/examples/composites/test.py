"""Composites example: the object-of-objects ``kind = "handle"`` generator.

A handle module wraps **one opaque hand-C resource** as a single typed CPython
class — the RAII shape (a file writer, a socket, a session). This example
vendors a small ring-buffer resource in C and declares a `kind = "handle"`
module over it; `jm apply` materializes the whole binding (the typed `Ring`
class, its constructor, methods, decoded-getter properties, and the
context-manager / ``close()`` protocol) so the only hand-written code is the
ring-buffer C itself.

It is the focused, teaching counterpart to the capsule/composer family — see
`docs/object-of-objects.md` for the full capsule/composer/handle depth. The
end-to-end build/import harness here is the same one `tests/test_handle_build.py`
uses, run through the cmake path the other examples build with.

Generated project (one handle module, `ring`):
  - Ring(capacity)            — `create_fn = ringbuf_open`
  - push(x: float[]) -> int   — array-in method, returns the count accepted
  - pop(n: int) -> float[]    — int-in -> independent numpy-owned array (FIFO)
  - .used / .fill_fraction    — decoded-getter properties (a live `*_stats_t`)
  - .gain  (read/write)       — a writable scalar property (getter + setter)
  - with Ring(...) as r: ...  — context manager + idempotent close()

The ring-buffer C is vendored as a `[project] c_deps` OBJECT library
(`native/src/ringbuf/`, a `ringbuf_core` lib, no Python wrapper); the handle
module's `depends_on = [{ name = "ringbuf", link = true }]` links that core
onto the module `.so`. The module is named `ring` (distinct from the `ringbuf`
backing) so the c_dep subdirectory and the handle target never collide, and
`package = "."` lands the `.so` in the package root so the import is the clean
`from composites.ring import Ring`.

Called by tests/test_examples.py via run(root). Also runnable directly:
    python3 examples/composites/test.py
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


def _cmd(args, cwd, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


# ── the vendored ring-buffer resource (a [project] c_deps OBJECT lib) ─────────
# A real FIFO ring buffer — the only hand-written code in the project. The
# generated `Ring` class is pure glue over this API: `ringbuf_open`/`_close`
# are the create/close lifecycle, `_push`/`_pop` the array methods, `_stats`
# fills a live struct the decoded getters read, and `_get_gain`/`_set_gain`
# back the writable `gain` property.
_RINGBUF_H = """\
#ifndef RINGBUF_H
#define RINGBUF_H
#include <stddef.h>
typedef struct ringbuf ringbuf_t;
typedef struct { size_t used; } ringbuf_stats_t;
ringbuf_t *ringbuf_open(size_t capacity);
void ringbuf_close(ringbuf_t *r);
size_t ringbuf_push(ringbuf_t *r, const float *x, size_t n);
size_t ringbuf_pop(ringbuf_t *r, float *out, size_t n);
void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out);
float ringbuf_get_gain(const ringbuf_t *r);
void ringbuf_set_gain(ringbuf_t *r, float gain);
#endif
"""

_RINGBUF_C = """\
/* A small FIFO ring buffer: push scales by gain, pop drains oldest-first. */
#include "ringbuf/ringbuf.h"
#include <stdlib.h>

struct ringbuf {
    float *buf;
    size_t cap, used, head;
    float gain;
};

ringbuf_t *ringbuf_open(size_t capacity)
{
    if (!capacity)
        return NULL;
    ringbuf_t *r = calloc(1, sizeof *r);
    if (!r)
        return NULL;
    r->buf = malloc(capacity * sizeof *r->buf);
    if (!r->buf) {
        free(r);
        return NULL;
    }
    r->cap = capacity;
    r->gain = 1.0f;
    return r;
}

void ringbuf_close(ringbuf_t *r)
{
    if (r) {
        free(r->buf);
        free(r);
    }
}

size_t ringbuf_push(ringbuf_t *r, const float *x, size_t n)
{
    size_t k = 0;
    for (; k < n && r->used < r->cap; k++) {
        r->buf[(r->head + r->used) % r->cap] = x[k] * r->gain;
        r->used++;
    }
    return k;
}

size_t ringbuf_pop(ringbuf_t *r, float *out, size_t n)
{
    size_t g = 0;
    for (; g < n && r->used > 0; g++) {
        out[g] = r->buf[r->head];
        r->head = (r->head + 1) % r->cap;
        r->used--;
    }
    return g;
}

void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out)
{
    out->used = r->used;
}

float ringbuf_get_gain(const ringbuf_t *r)
{
    return r->gain;
}

void ringbuf_set_gain(ringbuf_t *r, float gain)
{
    r->gain = gain;
}
"""

_RINGBUF_CMAKE = """\
# Vendored ring-buffer resource — pure C OBJECT lib, no Python wrapper.
# The handle module links `ringbuf_core` via its depends_on; the include path
# reaches the public header under native/inc/ringbuf/.
add_library(ringbuf_core OBJECT ringbuf.c)
target_include_directories(ringbuf_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
"""


def _ring_module() -> dict:
    """The `kind = "handle"` manifest section for the `ring` module.

    A focused subset of the handle feature family: a `create_fn` constructor,
    one array-in method (`push`), one int-in -> array-out method (`pop`), two
    decoded-getter properties off a live `*_stats_t` getter (`used`,
    `fill_fraction`), a writable scalar property (`gain`), and the
    context-manager / `close()` RAII protocol. See `docs/object-of-objects.md`
    for the broader capsule/composer/handle surface."""
    return {
        "kind": "handle",
        "backing": "ringbuf",
        "header": "ringbuf/ringbuf.h",
        "package": ".",  # land ring.so in the package root: composites.ring
        "type_name": "Ring",
        "context_manager": True,
        "create_fn": "ringbuf_open",
        "close_fn": "ringbuf_close",
        # link the vendored c_dep core onto the module .so (jm owns the link).
        "depends_on": [{"name": "ringbuf", "link": True}],
        "create_args": [{"name": "capacity", "type": "size_t"}],
        "methods": [
            {
                # array-in -> scalar count accepted (drops on a full buffer)
                "name": "push",
                "fn": "ringbuf_push",
                "returns": "size_t",
                "args": [{"name": "x", "type": "float[]"}],
            },
            {
                # int-in -> an independent numpy-owned array (FIFO oldest-first)
                "name": "pop",
                "fn": "ringbuf_pop",
                "returns": "float[]",
                "args": [{"name": "n", "type": "size_t"}],
            },
        ],
        "getters": [
            {
                # a live shared-struct getter feeding two decoded properties:
                # `used` reads a field directly; `fill_fraction` is a derived
                # `expr` over the field plus a stashed constructor value.
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
                # a scalar (return-by-value) getter whose field names a setter
                # -> a read/write `gain` property.
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


def run(root: Path) -> None:
    from just_makeit import _config as C
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new

    def q(fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    proj = root / "composites"

    # 1. Empty project — manifest only.
    q(jm_new, "composites", proj)

    # 2. Vendor the ring-buffer resource: a public header under native/inc and
    #    a pure-C OBJECT lib under native/src (the c_deps shape).
    inc = proj / "native" / "inc" / "ringbuf"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "ringbuf.h").write_text(_RINGBUF_H, encoding="utf-8")
    rb = proj / "native" / "src" / "ringbuf"
    rb.mkdir(parents=True, exist_ok=True)
    (rb / "ringbuf.c").write_text(_RINGBUF_C, encoding="utf-8")
    (rb / "CMakeLists.txt").write_text(_RINGBUF_CMAKE, encoding="utf-8")

    # 3. Declare the c_dep and the handle module, then apply: jm materializes
    #    the whole binding (ext.c + CMakeLists + .pyi) from the manifest.
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["ringbuf"]
    cfg.setdefault("module", {})["ring"] = _ring_module()
    C.save(proj, cfg)
    q(jm_apply, proj)

    # 4. Sanity-check the generated glue before building.
    ext = (proj / "native" / "src" / "ring" / "ring_ext.c").read_text("utf-8")
    assert "ringbuf_open" in ext  # create_fn wired into tp_init
    assert "PyInit_ring" in ext  # module init symbol matches the leaf
    ring_cmake = (proj / "native/src/ring/CMakeLists.txt").read_text("utf-8")
    assert "ringbuf_core" in ring_cmake  # depends_on link = true
    pyi = (proj / "src/composites/ring.pyi").read_text("utf-8")
    assert "class Ring" in pyi and "@gain.setter" in pyi  # writable property
    top = (proj / "CMakeLists.txt").read_text("utf-8")
    assert "add_subdirectory(native/src/ringbuf)" in top  # c_dep wired
    assert "add_subdirectory(native/src/ring)" in top  # handle module wired

    # 5. cmake configure + build (links ringbuf_core onto ring.so).
    build = proj / "build"
    _cmd(
        [
            "cmake",
            "-S",
            str(proj),
            "-B",
            str(build),
            "-DBUILD_PYTHON=ON",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
    )
    _cmd(["cmake", "--build", str(build), "-j"], cwd=proj)

    # 6. Exercise the typed Ring class through the built .so.
    env = {**os.environ, "PYTHONPATH": str(proj / "src")}
    _cmd([sys.executable, str(HERE / "smoke.py")], cwd=proj, env=env)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("composites: PASSED")
