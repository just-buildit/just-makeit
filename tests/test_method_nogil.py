"""`nogil` — generate a GIL-released kernel call in a variable_output method.

Releasing the GIL across the pure-C kernel lets a thread-per-shard worker
(one object + buffer per thread) scale across cores instead of serialising on
the GIL. jm generates the `Py_BEGIN_ALLOW_THREADS` wrapper — with the numpy
buffer accessors hoisted out first so no Python C-API runs while the GIL is
dropped — instead of the binding being hand-patched.
"""

import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._context._methods import (  # noqa: E402
    make_methods_ctx,
    _hoist_for_nogil,
)


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


_EXEC_METHOD = {
    "name": "execute",
    "return_type": "float _Complex",
    "variable_output": True,
    "pass_capacity": True,
    "params": [{"name": "x", "type": "float _Complex[]"}],
}


def _scaffold_with_execute(dest: Path, nogil: bool):
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "sig")
    _silent(
        object_run,
        dest,
        "dec",
        module="sig",
        no_state=True,
        no_step=True,
        init_params=[("rate", "float", "0.5")],
    )
    cfg = C.load(dest)
    m = dict(_EXEC_METHOD)
    if nogil:
        m["nogil"] = True
    cfg["dec"].setdefault("methods", []).append(m)
    C.save(dest, cfg)
    # Drop the freshly-scaffolded fragment so apply regenerates it with the
    # new method (per-object fragments are not re-rendered once they exist).
    (dest / "native/src/sig/sig_ext_dec.c").unlink()
    _silent(apply_run, dest)
    return (dest / "native/src/sig/sig_ext_dec.c").read_text(encoding="utf-8")


# ── (a) nogil generates the GIL-released kernel call ─────────────────────────
def test_nogil_generates_allow_threads(tmp_path):
    # gh-219 follow-up: _EXEC_METHOD's single-array-param shape is now
    # out=-eligible, so this fragment has TWO nogil-wrapped kernel-call
    # sites: the out= branch's (no realloc needed -- it writes into the
    # caller's buffer directly) and the default path's (realloc-before-nogil
    # for the internal grow-on-demand buffer). Check both independently
    # rather than assuming there's only one.
    frag = _scaffold_with_execute(tmp_path / "p", nogil=True)
    assert frag.count("Py_BEGIN_ALLOW_THREADS") == 2
    assert frag.count("Py_END_ALLOW_THREADS") == 2
    begins = [
        i
        for i in range(len(frag))
        if frag.startswith("Py_BEGIN_ALLOW_THREADS", i)
    ]
    ends = [
        i
        for i in range(len(frag))
        if frag.startswith("Py_END_ALLOW_THREADS", i)
    ]
    out_b, default_b = begins
    out_e, default_e = ends
    assert out_b < out_e < default_b < default_e
    # nothing Python-C-API runs while the GIL is dropped, in either block.
    for b, e in ((out_b, out_e), (default_b, default_e)):
        assert "PyArray_" not in frag[b:e]
        assert "Py_" not in frag[b + len("Py_BEGIN_ALLOW_THREADS") : e]
    # numpy accessors are hoisted BEFORE the default block...
    assert frag.index("_ng0") < default_b
    # ...and so is the allocation, which stays above it (under the GIL).
    # gh-604 removed the reuse buffer's realloc-based grow path entirely, so
    # what must precede the block is now the per-call PyArray_SimpleNew.
    assert frag.index("PyArray_SimpleNew(") < default_b
    # the out= branch is genuinely zero-alloc: it validates and writes into
    # the caller's buffer, allocating nothing between its own markers.
    assert "PyArray_SimpleNew(" not in frag[out_b:out_e]
    assert "malloc(" not in frag[out_b:out_e]


# ── (b) without nogil the binding is unchanged (no regression) ───────────────
def test_without_nogil_no_allow_threads(tmp_path):
    frag = _scaffold_with_execute(tmp_path / "p", nogil=False)
    assert "Py_BEGIN_ALLOW_THREADS" not in frag
    assert "size_t n_out = dec_execute(" in frag  # plain kernel call


# ── (c) idempotent ───────────────────────────────────────────────────────────
def test_nogil_idempotent(tmp_path):
    dest = tmp_path / "p"
    first = _scaffold_with_execute(dest, nogil=True)
    _silent(apply_run, dest)
    assert (dest / "native/src/sig/sig_ext_dec.c").read_text(
        encoding="utf-8"
    ) == first


# ── (d) manifest round-trips nogil ───────────────────────────────────────────
def test_nogil_manifest_roundtrip(tmp_path):
    dest = tmp_path / "p"
    _scaffold_with_execute(dest, nogil=True)
    cfg = C.load(dest)
    exec_m = next(m for m in cfg["dec"]["methods"] if m["name"] == "execute")
    assert exec_m.get("nogil") is True
    assert "nogil = true" in C._dump(cfg)


# ── unit tests for the hoister + context builder ─────────────────────────────
def test_hoist_lifts_only_numpy_accessors():
    call = (
        "dec_execute(self->handle,"
        " (const float complex *)PyArray_DATA(x_arr),"
        " (size_t)PyArray_SIZE(x_arr), self->_execute_buf,"
        " self->_execute_buf_cap)"
    )
    decls, rewritten = _hoist_for_nogil(call)
    # two accessors hoisted, with their casts preserved as the local types
    assert "const float complex * _ng0 = (const float complex *)" in decls
    assert "size_t _ng1 = (size_t)PyArray_SIZE(x_arr);" in decls
    assert "PyArray_" not in rewritten  # all accessors replaced
    # plain memory operands are left untouched
    assert "self->handle" in rewritten
    assert "self->_execute_buf" in rewritten


def test_make_methods_ctx_nogil_off_is_plain():
    ctx = make_methods_ctx(
        "dec", "Dec", [dict(_EXEC_METHOD)], pkg="p", no_state=True
    )
    assert "Py_BEGIN_ALLOW_THREADS" not in ctx["extra_methods_c"]


def test_make_methods_ctx_nogil_on_wraps_kernel():
    m = dict(_EXEC_METHOD)
    m["nogil"] = True
    ctx = make_methods_ctx("dec", "Dec", [m], pkg="p", no_state=True)
    assert "Py_BEGIN_ALLOW_THREADS" in ctx["extra_methods_c"]


# ── result_fields (multi-result "push") honours nogil too ────────────────────
# Regression: the max_results/result_fields binding hardcoded the kernel call
# and ignored `nogil`, so detector-style push methods ran holding the GIL.
_PUSH_METHOD = {
    "name": "push",
    "arg_type": "float _Complex",
    "return_type": "rec_t",
    "max_results": 64,
    "result_fields": [
        {"name": "lag", "type": "size_t"},
        {"name": "stat", "type": "float"},
    ],
}


def test_make_methods_ctx_result_fields_nogil_off_is_plain():
    ctx = make_methods_ctx(
        "dec", "Dec", [dict(_PUSH_METHOD)], pkg="p", no_state=True
    )
    c = ctx["extra_methods_c"]
    assert "Py_BEGIN_ALLOW_THREADS" not in c
    assert "size_t n_out = dec_push(" in c  # plain kernel call


def test_make_methods_ctx_result_fields_nogil_wraps_kernel():
    m = dict(_PUSH_METHOD)
    m["nogil"] = True
    ctx = make_methods_ctx("dec", "Dec", [m], pkg="p", no_state=True)
    c = ctx["extra_methods_c"]
    assert "Py_BEGIN_ALLOW_THREADS" in c
    assert "Py_END_ALLOW_THREADS" in c
    b = c.index("Py_BEGIN_ALLOW_THREADS")
    e = c.index("Py_END_ALLOW_THREADS")
    # numpy accessor hoisted above the block; none runs while the GIL is dropped
    assert "_ng0" in c and c.index("_ng0") < b
    assert "PyArray_" not in c[b:e]
    # the results[] buffer is declared before the block; DECREF stays after it
    assert c.index("results[64]") < b
    assert c.index("Py_DECREF(in_arr)") > e
