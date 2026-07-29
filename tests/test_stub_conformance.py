"""Stub conformance gate: every importable symbol has a matching stub.

jm generates a `.pyi` for every object/module, but nothing verified it agreed
with the compiled extension. The only stub tests were `ast.parse` (syntax) and
per-feature substring asserts, so the same class of bug kept recurring one
issue at a time (gh-446, gh-519, gh-527, gh-529, gh-530, gh-543 were all stub
defects). This gate closes the class: for each emit-path shape it scaffolds a
minimal module, builds it, and runs `mypy.stubtest`, which imports the `.so`,
walks every public symbol, and fails on anything importable-but-unstubbed or
any stub that disagrees with the runtime.

Two mechanics matter:

* **Isolation.** stubtest imports the whole package, and a scaffold's generated
  `tests/` / `benchmarks/` carry an unguarded `import pytest` and (for
  pytest-benchmark projects) type errors that abort mypy's build before it can
  compare anything. So each case copies just `<leaf>.<so>` + `<leaf>.pyi` into a
  clean directory and points stubtest there.
* **Interpreter match.** The extension is built with
  `-DPython3_EXECUTABLE={sys.executable}` so the `.so` imports under the same
  interpreter that runs stubtest.

The whole gate self-skips when cmake / a C compiler / numpy / mypy is absent,
mirroring `tests/test_examples.py`.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._function import run as function_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._view import run as view_run


# ── skip guard ──────────────────────────────────────────────────────────────


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    try:
        import mypy.stubtest  # noqa: F401
    except ImportError:
        return "mypy not importable"
    return None


_SKIP = _skip_reason()


# ── harness ─────────────────────────────────────────────────────────────────


def _q(fn, *a, **k):
    """Call a jm command entry point with its scaffold chatter suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _build(root: Path) -> None:
    build = root / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, (
            f"{cmd[0]} {cmd[1]} failed:\n{r.stdout}\n{r.stderr}"
        )


def _stubtest(so_dir: Path, leaf: str) -> list[str]:
    """Isolate <leaf>.so + <leaf>.pyi and return stubtest's error lines.

    The isolation dir is a context manager, not a bare ``mkdtemp``: stubtest
    runs with ``cwd`` set here and drops a ``.mypy_cache`` (~13 MB) beside the
    copied module, so one leaked dir per shape per run adds up fast — 12 GB of
    ``/tmp`` had accumulated before this was caught.
    """
    sos = list(so_dir.glob(f"{leaf}.*.so"))
    assert sos, f"no built {leaf}.*.so in {so_dir}"
    pyi = so_dir / f"{leaf}.pyi"
    assert pyi.exists(), f"no {leaf}.pyi in {so_dir}"

    with tempfile.TemporaryDirectory() as tmp:
        iso = Path(tmp)
        shutil.copy2(sos[0], iso / sos[0].name)
        shutil.copy2(pyi, iso / pyi.name)

        r = subprocess.run(
            [sys.executable, "-m", "mypy.stubtest", leaf],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(iso),
            env={**os.environ, "PYTHONPATH": str(iso), "MYPYPATH": str(iso)},
        )
    # Both stubtest's own `error:` lines and the underlying `<file>: error:`
    # lines it prints when the stub is not even mypy-valid ("not checking stubs
    # due to mypy build errors") count as failures.
    return [ln for ln in r.stdout.splitlines() if "error:" in ln]


def _check(root: Path, so_dir: Path, leaf: str) -> None:
    _build(root)
    errs = _stubtest(so_dir, leaf)
    assert not errs, "stub does not match runtime:\n" + "\n".join(errs)


# ── shape builders ──────────────────────────────────────────────────────────
#
# Each returns (root, so_dir, leaf). A standalone object's module IS the
# component (so_dir=src/<pkg>/, leaf=<comp>); a module object's leaf is the
# module name (so_dir=src/<pkg>/<leaf>/).


def _pkg(tmp: Path) -> Path:
    return tmp / "proj"


def shape_standalone_state(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    return d, d / "src" / "proj", "osc"


def shape_module_state(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    return d, d / "src" / "proj" / "widget", "widget"


def shape_standalone_method(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    _q(
        method_run,
        d,
        "osc",
        "tweak",
        None,
        "float",
        "float",
        False,
        [],
        params=[("k", "double")],
    )
    return d, d / "src" / "proj", "osc"


def shape_module_method(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    _q(
        method_run,
        d,
        "gizmo",
        "tweak",
        "widget",
        "float",
        "float",
        False,
        [],
        params=[("k", "double")],
    )
    return d, d / "src" / "proj" / "widget", "widget"


def shape_standalone_property_computed(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    _q(property_run, d, "osc", "ready", None, "bool", False)
    # A computed property's accessor is the user's to implement (jm only
    # declares it), so supply a trivial body or the .so won't link.
    core = d / "native" / "src" / "osc" / "osc_core.c"
    core.write_text(
        core.read_text()
        + "\nbool osc_get_ready(const osc_state_t *state)\n"
        + "{ (void)state; return true; }\n"
    )
    return d, d / "src" / "proj", "osc"


def shape_standalone_function(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    _q(
        function_run,
        d,
        "scale",
        "widget",
        params=[("x", "double")],
        return_type="double",
        impl_body="    return x * 2.0;",
    )
    return d, d / "src" / "proj" / "widget", "widget"


def _mod(tmp, obj="gizmo", state=(("gain", "double", "1.0"),), **objkw):
    """Scaffold a module `widget` with one object; return (root, so_dir, leaf)."""
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", [obj])
    _q(object_run, d, obj, module="widget", state_vars=list(state), **objkw)
    return d, d / "src" / "proj" / "widget", "widget"


def _append_core(root, comp, code):
    """Append a C body to a component's sacred `_core.c`."""
    core = root / "native" / "src" / comp / f"{comp}_core.c"
    core.write_text(core.read_text() + "\n" + code + "\n")


def _append_header(root, comp, code):
    """Append a declaration to a component's `_core.h`, before the include
    guard's closing `#endif` so a sibling `_ext` fragment sees it."""
    h = root / "native" / "inc" / comp / f"{comp}_core.h"
    text = h.read_text()
    idx = text.rfind("#endif")
    h.write_text(text[:idx] + code + "\n\n" + text[idx:])


def _write_enum(root, name, values):
    from just_makeit import _config as C

    vals = ", ".join(f'"{v}"' for v in values)
    with (root / C.FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(f'\n[[enum]]\nname = "{name}"\nvalues = [{vals}]\n')
    _q(apply_run, root)


# ── property kinds ──────────────────────────────────────────────────────────


def shape_module_property_field(tmp):
    d, so, leaf = _mod(tmp)
    _q(
        property_run,
        d,
        "gizmo",
        "depth",
        "widget",
        "size_t",
        False,
        field=True,
    )
    return d, so, leaf


def shape_standalone_property_expr(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    # An `--expr` property is inlined into the _ext.c getter, whose state
    # pointer is `self->handle` (not a `state` alias).
    _q(
        property_run,
        d,
        "osc",
        "hot",
        None,
        "bool",
        False,
        expr="self->handle->gain > 0.0",
    )
    return d, d / "src" / "proj", "osc"


def shape_module_property_enum(tmp):
    d, so, leaf = _mod(tmp, state=[("mode", "int", "0")])
    _write_enum(d, "mode_kind", ["slow", "fast"])
    _q(
        property_run,
        d,
        "gizmo",
        "mode",
        "widget",
        "int",
        True,
        field=True,
        enum="mode_kind",
    )
    return d, so, leaf


# ── lifecycle ───────────────────────────────────────────────────────────────


def shape_module_no_reset(tmp):
    return _mod(tmp, no_reset=True)


def shape_module_streamable(tmp):
    # A generator object (void arg) is the natural streamable shape.
    return _mod(
        tmp, arg_type="void", return_type="float _Complex", streamable=True
    )


def shape_module_async_stream(tmp):
    return _mod(
        tmp,
        arg_type="void",
        return_type="float _Complex",
        streamable=True,
        async_stream=True,
    )


def shape_module_serializable(tmp):
    d, so, leaf = _mod(tmp, serializable=True)
    # serializable generates the state-blob binding but calls a hand-written
    # triplet (gh-400) the user declares in the sacred header and implements.
    _append_header(
        d,
        "gizmo",
        "size_t gizmo_state_bytes(const gizmo_state_t *s);\n"
        "void gizmo_get_state(const gizmo_state_t *s, char *out);\n"
        "int gizmo_set_state(gizmo_state_t *s, const char *in);",
    )
    _append_core(
        d,
        "gizmo",
        "size_t gizmo_state_bytes(const gizmo_state_t *s)\n"
        "{ (void)s; return sizeof(double); }\n"
        "void gizmo_get_state(const gizmo_state_t *s, char *out)\n"
        "{ *(double *)out = s->gain; }\n"
        "int gizmo_set_state(gizmo_state_t *s, const char *in)\n"
        "{ s->gain = *(const double *)in; return 0; }",
    )
    return d, so, leaf


# ── methods ─────────────────────────────────────────────────────────────────


def shape_module_method_out_type(tmp):
    d, so, leaf = _mod(tmp)
    _q(
        method_run,
        d,
        "gizmo",
        "render",
        "widget",
        "void",
        "void",
        False,
        [],
        params=[("n", "size_t")],
        out_type="float _Complex",
    )
    return d, so, leaf


def shape_module_method_variable_output(tmp):
    d, so, leaf = _mod(tmp)
    _q(
        method_run,
        d,
        "gizmo",
        "emit",
        "widget",
        "void",
        "float _Complex",
        True,
        [],
        params=[("n", "size_t")],
    )
    # jm stubs both the kernel and <comp>_<name>_max_out() in _core.c, so the
    # scaffold links as-is (the gate imports the module, it does not call it).
    return d, so, leaf


# ── ctor / state ────────────────────────────────────────────────────────────


def shape_module_init_params_path(tmp):
    # doppler's reader shape: no_state + a path init-param.
    return _mod(
        tmp,
        state=[],
        no_state=True,
        init_params=[("path", "path", "")],
    )


def shape_module_init_params_bytes(tmp):
    # gh-565: doppler's Plan restore shape — no_state + an opaque bytes
    # init-param that expands to (const void *, size_t) and parses via y#.
    return _mod(
        tmp,
        state=[],
        no_state=True,
        init_params=[("blob", "bytes", "")],
    )


def shape_module_state_plus_initparams(tmp):
    return _mod(
        tmp,
        state=[("cap", "size_t", "16")],
        init_params=[("path", "path", "")],
    )


def shape_module_view(tmp):
    # A view is a second class over the shared core, built by a create_fn.
    d, so, leaf = _mod(tmp)
    _q(view_run, d, "gizmo", "Peek", "widget", "gizmo_make_peek")
    # jm declares and stubs the create_fn body, so the scaffold links as-is
    # (the gate imports the module, it does not construct the view).
    return d, so, leaf


def shape_standalone_array_state(tmp):
    d = _pkg(tmp)
    _q(
        new_run,
        "proj",
        d,
        ["fir"],
        [("taps", "double[4]", ""), ("gain", "double", "1.0")],
    )
    return d, d / "src" / "proj", "fir"


# ── module kinds (slice 3) ───────────────────────────────────────────────────
#
# The capsule/handle/composer kinds each have their OWN `.pyi` generator
# (`_handle.py` / `_capsule.py` / `_composer.py`), wholly separate from the
# object/module path the shapes above exercise — so they are the highest-odds
# location for undiscovered stub drift. Unlike an object (jm owns the C core),
# a handle wraps a hand-C backing, so the shape vendors a tiny real resource as
# a `[project] c_deps` OBJECT lib and links it in, exactly as the `composites`
# example and `tests/test_handle_build.py` do.

_RINGBUF_H = """\
#ifndef RINGBUF_H
#define RINGBUF_H
#include <stddef.h>
typedef struct ringbuf ringbuf_t;
typedef struct { size_t used; } ringbuf_stats_t;
ringbuf_t *ringbuf_open(size_t capacity);
void ringbuf_close(ringbuf_t *r);
size_t ringbuf_push(ringbuf_t *r, const float *x, size_t n);
size_t ringbuf_push_gain(ringbuf_t *r, const float *x, size_t n, float gain);
size_t ringbuf_pop(ringbuf_t *r, float *out, size_t n);
void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out);
float ringbuf_get_gain(const ringbuf_t *r);
void ringbuf_set_gain(ringbuf_t *r, float gain);
size_t ringbuf_save_bytes(const ringbuf_t *r);
size_t ringbuf_save(const ringbuf_t *r, void *out);
ringbuf_t *ringbuf_restore(const void *blob, size_t n);
#endif
"""

_RINGBUF_C = """\
#include "ringbuf/ringbuf.h"
#include <stdlib.h>
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
void ringbuf_stats(const ringbuf_t *r, ringbuf_stats_t *out) {
    out->used = r->used;
}
float ringbuf_get_gain(const ringbuf_t *r) { return r->gain; }
void ringbuf_set_gain(ringbuf_t *r, float gain) { r->gain = gain; }
size_t ringbuf_save_bytes(const ringbuf_t *r) {
    return r->used * sizeof(float);
}
size_t ringbuf_save(const ringbuf_t *r, void *out) {
    size_t n = r->used * sizeof(float);
    char *dst = (char *)out;
    for (size_t i = 0; i < r->used; i++) {
        float v = r->buf[(r->head + i) % r->cap];
        __builtin_memcpy(dst + i * sizeof(float), &v, sizeof(float));
    }
    return n;
}
ringbuf_t *ringbuf_restore(const void *blob, size_t n) {
    size_t count = n / sizeof(float);
    ringbuf_t *r = ringbuf_open(count ? count : 1);
    if (!r) return NULL;
    ringbuf_push(r, (const float *)blob, count);
    return r;
}
"""

_RINGBUF_CMAKE = """\
add_library(ringbuf_core OBJECT ringbuf.c)
target_include_directories(ringbuf_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
"""


def _vendor_ringbuf(proj: Path) -> None:
    """Drop the ringbuf c_dep (public header + OBJECT-lib source) into *proj*."""
    inc = proj / "native" / "inc" / "ringbuf"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "ringbuf.h").write_text(_RINGBUF_H, encoding="utf-8")
    rb = proj / "native" / "src" / "ringbuf"
    rb.mkdir(parents=True, exist_ok=True)
    (rb / "ringbuf.c").write_text(_RINGBUF_C, encoding="utf-8")
    (rb / "CMakeLists.txt").write_text(_RINGBUF_CMAKE, encoding="utf-8")


def _inject_module(
    proj: Path,
    module: str,
    section: dict,
    c_dep: str,
    enums: list[dict] | None = None,
) -> None:
    """Declare the *c_dep* OBJECT lib + a kind-module section, then apply.

    *enums* populates the top-level ``[[enum]]`` SSOT, which the composer kind
    needs — its source discriminant and timeline loop are enum-validated.
    """
    from just_makeit import _config as C

    cfg = C.load(proj)
    cfg["project"]["c_deps"] = [c_dep]
    if enums:
        cfg["enum"] = enums
    cfg.setdefault("module", {})[module] = section
    C.save(proj, cfg)
    _q(apply_run, proj)


def shape_handle(tmp):
    """A `kind = "handle"` module: a typed class over an opaque hand-C resource.

    Exercises `_handle.py`'s dedicated `.pyi` generator — create_fn ctor,
    context-manager protocol, an array-in method, an array+scalar-default
    method, an int-in -> array-out method, a decoded-getter property, and a
    writable scalar property. `package = "."` lands `ring.so` in the package
    root so the leaf import is `proj.ring` (stubtest target `ring`)."""
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _vendor_ringbuf(d)
    _inject_module(
        d,
        "ring",
        {
            "kind": "handle",
            "backing": "ringbuf",
            "header": "ringbuf/ringbuf.h",
            "package": ".",
            "type_name": "Ring",
            "context_manager": True,
            "create_fn": "ringbuf_open",
            "close_fn": "ringbuf_close",
            "depends_on": [{"name": "ringbuf", "link": True}],
            "create_args": [{"name": "capacity", "type": "size_t"}],
            # gh-565 slice 3: a module-level factory (alternate constructor).
            "factories": [
                {
                    "name": "RingFromBlob",
                    "create_fn": "ringbuf_restore",
                    "init_params": [{"name": "blob", "type": "bytes"}],
                }
            ],
            "methods": [
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
                    "name": "push_gain",
                    "fn": "ringbuf_push_gain",
                    "returns": "size_t",
                    "args": [
                        {"name": "x", "type": "float[]"},
                        {"name": "gain", "type": "float", "default": "1.0f"},
                    ],
                },
                {
                    "name": "pop",
                    "fn": "ringbuf_pop",
                    "returns": "float[]",
                    "args": [{"name": "n", "type": "size_t"}],
                },
            ],
            "getters": [
                {
                    "fn": "ringbuf_stats",
                    "out": "ringbuf_stats_t",
                    "cache": False,
                    "fields": [
                        {"name": "used", "from": "used", "type": "size_t"}
                    ],
                },
                {
                    "fn": "ringbuf_get_gain",
                    "out": "float",
                    "fields": [
                        {
                            "name": "gain",
                            "type": "float",
                            "writable_fn": "ringbuf_set_gain",
                        }
                    ],
                },
            ],
        },
        c_dep="ringbuf",
    )
    return d, d / "src" / "proj", "ring"


# A minimal backing for the capsule shape: the exact C API the generated
# capsule ext expects — `<b>_create(init...)` -> `<b>_state_t *`, a
# variable-output `<b>_execute(state, const IN*, n_in, OUT*, max_out)`, a void
# `<b>_reset(state)`, `<b>_destroy(state)`, and scalar get/set accessors.
_GADGET_H = """\
#ifndef GADGET_H
#define GADGET_H
#include <stddef.h>
#include <complex.h>
typedef struct gadget gadget_state_t;
gadget_state_t *gadget_create(double gain);
void gadget_destroy(gadget_state_t *s);
void gadget_reset(gadget_state_t *s);
size_t gadget_execute(gadget_state_t *s, const float *in, size_t n_in,
                      float _Complex *out, size_t max_out);
double gadget_get_gain(const gadget_state_t *s);
void gadget_set_gain(gadget_state_t *s, double gain);
#endif
"""

_GADGET_C = """\
#include "gadget/gadget.h"
#include <stdlib.h>
struct gadget { double gain; };
gadget_state_t *gadget_create(double gain) {
    gadget_state_t *s = calloc(1, sizeof *s);
    if (s) s->gain = gain;
    return s;
}
void gadget_destroy(gadget_state_t *s) { free(s); }
void gadget_reset(gadget_state_t *s) { s->gain = 0.0; }
size_t gadget_execute(gadget_state_t *s, const float *in, size_t n_in,
                      float _Complex *out, size_t max_out) {
    size_t k = 0;
    for (; k < n_in && k < max_out; k++)
        out[k] = (float _Complex)(in[k] * s->gain);
    return k;
}
double gadget_get_gain(const gadget_state_t *s) { return s->gain; }
void gadget_set_gain(gadget_state_t *s, double gain) { s->gain = gain; }
"""

_GADGET_CMAKE = """\
add_library(gadget_core OBJECT gadget.c)
target_include_directories(gadget_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
"""


def _vendor_gadget(proj: Path) -> None:
    """Drop the gadget c_dep (public header + OBJECT-lib source) into *proj*."""
    inc = proj / "native" / "inc" / "gadget"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "gadget.h").write_text(_GADGET_H, encoding="utf-8")
    src = proj / "native" / "src" / "gadget"
    src.mkdir(parents=True, exist_ok=True)
    (src / "gadget.c").write_text(_GADGET_C, encoding="utf-8")
    (src / "CMakeLists.txt").write_text(_GADGET_CMAKE, encoding="utf-8")


def shape_capsule(tmp):
    """A `kind = "capsule"` module: free functions over an opaque PyCapsule.

    Unlike handle/composer, a capsule exposes no Python *class* — its `.pyi`
    (from `_capsule.py`'s own generator) is module-level `create`/`execute`/
    `reset`/`destroy`/`get_`/`set_` functions over an opaque `Any` handle.
    The shape vendors a minimal `gadget` backing whose C API matches exactly
    what the generated ext calls, then stubtests the free-function surface."""
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _vendor_gadget(d)
    _inject_module(
        d,
        "wrap",
        {
            "kind": "capsule",
            "backing": "gadget",
            "capsule_name": "proj.wrap.gadget_state",
            "header": "gadget/gadget.h",
            "package": ".",
            "depends_on": [{"name": "gadget", "link": True}],
            "init_params": [{"name": "gain", "type": "double"}],
            "methods": [
                {
                    "name": "execute",
                    "arg_type": "float[]",
                    "return_type": "float _Complex[]",
                    "caller_out": True,
                    "nogil": True,
                },
                {"name": "reset"},
            ],
            "properties": [
                {"name": "gain", "type": "double", "writable": True},
            ],
        },
        c_dep="gadget",
    )
    return d, d / "src" / "proj", "wrap"


# ── composer (gh-560) ────────────────────────────────────────────────────────
#
# The last of the three module kinds to reach the gate. Composer is the widest
# `.pyi` surface jm emits — FOUR classes (source / segment / timeline /
# composer) from one generator — and until now it was only ever compiled against
# doppler's real `wfm_source_t`, so nothing here could build it.
#
# The backing is deliberately tiny. With `json.enabled` off the generated ext
# calls exactly four external symbols, and the struct layout is fixed by
# `_build_<backing>_segments`: a segment owns `sources` / `n_sources` plus its
# own declared fields, and a source is just its declared fields.

_MIXER_H = """\
#ifndef MIXER_H
#define MIXER_H
#include <stddef.h>
#include <complex.h>
typedef struct { int type; double freq; } mixer_source_t;
typedef struct {
    mixer_source_t *sources;
    size_t          n_sources;
    double          fs;
    size_t          num_samples;
} mixer_segment_t;
typedef struct mixer_state mixer_state_t;
mixer_state_t *mixer_create(const mixer_segment_t *segs, size_t n,
                            int repeat, int continuous);
void mixer_destroy(mixer_state_t *s);
size_t mixer_execute(mixer_state_t *s, float _Complex *out, size_t max);
const mixer_segment_t *mixer_segments(const mixer_state_t *s, size_t *n,
                                      int *repeat, int *continuous);
#endif
"""

# A real (if trivial) composer: it deep-copies the spec, then drains each
# segment's num_samples as a ramp scaled by the first source's freq. Enough
# behaviour that execute()/compose() return something a test can assert on,
# and enough lifetime handling that the gate exercises a genuine backing.
_MIXER_C = """\
#include "mixer/mixer.h"
#include <stdlib.h>
struct mixer_state {
    mixer_segment_t *segs;
    size_t           n;
    int              repeat, continuous;
    size_t           seg_i, pos;
};
mixer_state_t *mixer_create(const mixer_segment_t *segs, size_t n,
                            int repeat, int continuous) {
    mixer_state_t *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->segs = calloc(n ? n : 1, sizeof *s->segs);
    if (!s->segs) { free(s); return NULL; }
    for (size_t i = 0; i < n; i++) {
        s->segs[i] = segs[i];
        s->segs[i].sources = calloc(segs[i].n_sources ? segs[i].n_sources : 1,
                                    sizeof *s->segs[i].sources);
        if (!s->segs[i].sources) { mixer_destroy(s); return NULL; }
        for (size_t k = 0; k < segs[i].n_sources; k++)
            s->segs[i].sources[k] = segs[i].sources[k];
    }
    s->n = n; s->repeat = repeat; s->continuous = continuous;
    return s;
}
void mixer_destroy(mixer_state_t *s) {
    if (!s) return;
    for (size_t i = 0; i < s->n; i++) free(s->segs[i].sources);
    free(s->segs);
    free(s);
}
size_t mixer_execute(mixer_state_t *s, float _Complex *out, size_t max) {
    size_t k = 0;
    while (k < max) {
        if (s->seg_i >= s->n) {
            if (!s->repeat && !s->continuous) break;
            s->seg_i = 0; s->pos = 0;
            if (!s->n) break;
        }
        mixer_segment_t *sg = &s->segs[s->seg_i];
        if (s->pos >= sg->num_samples) { s->seg_i++; s->pos = 0; continue; }
        double f = sg->n_sources ? sg->sources[0].freq : 0.0;
        out[k++] = (float)(f * (double)s->pos) + 0.0f * _Complex_I;
        s->pos++;
    }
    return k;
}
const mixer_segment_t *mixer_segments(const mixer_state_t *s, size_t *n,
                                      int *repeat, int *continuous) {
    if (n) *n = s->n;
    if (repeat) *repeat = s->repeat;
    if (continuous) *continuous = s->continuous;
    return s->segs;
}
"""

_MIXER_CMAKE = """\
add_library(mixer_core OBJECT mixer.c)
target_include_directories(mixer_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)
"""


def _vendor_mixer(proj: Path) -> None:
    """Drop the mixer c_dep (public header + OBJECT-lib source) into *proj*."""
    inc = proj / "native" / "inc" / "mixer"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "mixer.h").write_text(_MIXER_H, encoding="utf-8")
    mx = proj / "native" / "src" / "mixer"
    mx.mkdir(parents=True, exist_ok=True)
    (mx / "mixer.c").write_text(_MIXER_C, encoding="utf-8")
    (mx / "CMakeLists.txt").write_text(_MIXER_CMAKE, encoding="utf-8")


def shape_composer(tmp):
    """A `kind = "composer"` module: the four-class object-of-objects surface.

    Exercises `_composer.py`'s own `.pyi` generator — a source type with an
    enum-validated discriminant and a factory, a multi-source segment, a
    timeline with a loop enum, and the composer itself with `execute` /
    `compose` / `stream`. `stream = true` is deliberate: it is the one shape
    whose iterator type is a non-subclassable `Py_TPFLAGS_DEFAULT` type object,
    so the stub's `Iterator[...]` return is checked against a real runtime."""
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _vendor_mixer(d)
    _inject_module(
        d,
        "mix",
        {
            "kind": "composer",
            "backing": "mixer",
            "capsule_name": "proj.mix.mixer_state",
            "header": "mixer/mixer.h",
            "package": ".",
            "depends_on": [{"name": "mixer", "link": True}],
            "composes": ["mixer_synth"],
            "source": {
                "object": "mixer_synth",
                "struct": "mixer_source_t",
                "type_name": "Synth",
                "fields": [
                    {
                        "name": "type",
                        "type": "int",
                        "enum": "wave",
                        "default": "tone",
                    },
                    {"name": "freq", "type": "double", "default": "0.0"},
                ],
            },
            "segment": {
                "type_name": "Segment",
                "struct": "mixer_segment_t",
                "fields": [
                    {"name": "fs", "type": "double", "default": "1e6"},
                    {
                        "name": "num_samples",
                        "type": "size_t",
                        "default": "64",
                    },
                ],
                "sources": "multi",
            },
            "timeline": {
                "type_name": "Timeline",
                "loop": ["once", "repeat"],
            },
            "oo": {
                "factories": ["tone"],
                "emit": "ctypes",
                "discriminant": "type",
                "composer_type_name": "Composer",
            },
            "composer": {"stream": True},
        },
        c_dep="mixer",
        enums=[{"name": "wave", "values": ["tone", "noise"]}],
    )
    return d, d / "src" / "proj", "mix"


_SHAPES = {
    "standalone_state": shape_standalone_state,
    "module_state": shape_module_state,
    "standalone_method": shape_standalone_method,
    "module_method": shape_module_method,
    "standalone_property_computed": shape_standalone_property_computed,
    "module_function": shape_standalone_function,
    # batch 2
    "module_property_field": shape_module_property_field,
    "standalone_property_expr": shape_standalone_property_expr,
    "module_property_enum": shape_module_property_enum,
    "module_no_reset": shape_module_no_reset,
    "module_streamable": shape_module_streamable,
    "module_async_stream": shape_module_async_stream,
    "module_serializable": shape_module_serializable,
    "module_method_out_type": shape_module_method_out_type,
    "module_method_variable_output": shape_module_method_variable_output,
    "module_init_params_path": shape_module_init_params_path,
    "module_init_params_bytes": shape_module_init_params_bytes,
    "module_state_plus_initparams": shape_module_state_plus_initparams,
    "module_view": shape_module_view,
    "standalone_array_state": shape_standalone_array_state,
    # batch 3 — module kinds (own .pyi generators)
    "handle": shape_handle,
    "capsule": shape_capsule,
    "composer": shape_composer,
}


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
@pytest.mark.parametrize("name", list(_SHAPES))
def test_stub_matches_runtime(name, tmp_path):
    root, so_dir, leaf = _SHAPES[name](tmp_path)
    _check(root, so_dir, leaf)
