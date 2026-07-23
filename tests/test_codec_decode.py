"""Slice 3 of the variant codec: the read-decode property (gh-554).

A container property with `codec` + an `entry_fn` cursor decodes each entry to
Python from the SAME `[codec.X]` table that drives the write pack — jm generates
the decode helper (the read mirror of the pack) and the precise-union `.pyi`, so
there is no hand `value_fn`. jm declares neither the entry_fn nor its struct (as
on the write side it never declares the sink) — the user owns those. The build
test decodes a real dict end-to-end.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _codec as K
from just_makeit import _config as C
from just_makeit import _context as Ctx
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run

_CODEC = {
    "discriminant": "char",
    "scalar_collapse": True,
    "entries": [
        {"code": "A", "ctype": "char", "bytes": True},
        {"code": "X", "ctype": "int64_t"},
        {"code": "D", "ctype": "double"},
    ],
}

_PROP = {
    "name": "keywords",
    "type": "dict",
    "codec": "kw",
    "count_fn": "gizmo_kw_count",
    "key_fn": "gizmo_kw_tag",
    "entry_fn": "gizmo_kw_entry",
    "entry_type": "gizmo_kw_t",
    "type_field": "type",
    "count_field": "count",
    "value_field": "value",
}


def _q(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ── generator unit (no compiler) ──────────────────────────────────────────────


class TestRenderDecode:
    def _ctx(self):
        return Ctx.make_properties_ctx(
            "gizmo", "Gizmo", [dict(_PROP)], codecs={"kw": _CODEC}
        )

    def test_c_has_decode_helper_and_switch(self):
        gs = self._ctx()["getset_def"]
        assert "Gizmo_decode_keywords" in gs
        assert "gizmo_kw_entry(self->handle, _i)" in gs
        assert "unknown code" in gs  # the switch default
        # bytes -> str; numeric decode of both kinds
        assert "PyUnicode_FromStringAndSize" in gs
        assert "PyLong_FromLongLong((long long)_v)" in gs
        assert "PyFloat_FromDouble((double)_v)" in gs

    def test_scalar_collapse_from_codec(self):
        gs = self._ctx()["getset_def"]
        assert "PyList_GET_ITEM(_lst, 0)" in gs  # count==1 -> scalar

    def test_no_scalar_collapse_when_off(self):
        cdc = {**_CODEC, "scalar_collapse": False}
        gs = Ctx.make_properties_ctx(
            "gizmo", "Gizmo", [dict(_PROP)], codecs={"kw": cdc}
        )["getset_def"]
        assert "PyList_GET_ITEM(_lst, 0)" not in gs

    def test_pyi_union_read_form(self):
        pyi = self._ctx()["property_stubs_pyi"]
        assert (
            "def keywords(self) -> dict[str, "
            "str | int | float | list[int] | list[float]]:" in pyi
        )

    def test_jm_does_not_declare_entry_fn(self):
        # the entry_fn/struct are the user's — jm emits no _core.h decl for them.
        _fn_c, _expr, decls = K.render_decode(
            "gizmo", "Gizmo", dict(_PROP), _CODEC
        )
        assert decls == []


# ── build + decode a real dict (needs a compiler) ─────────────────────────────

_STRUCT_AND_CURSOR = """
typedef struct { char tag[8]; char type; size_t count, elem_size;
                 unsigned char *value; } gizmo_kw_t;
size_t gizmo_kw_count(const gizmo_state_t *s);
const char *gizmo_kw_tag(const gizmo_state_t *s, size_t i);
const gizmo_kw_t *gizmo_kw_entry(const gizmo_state_t *s, size_t i);
"""

_CURSOR_IMPL = """
#include <string.h>
static gizmo_kw_t _KW[2];
size_t gizmo_kw_count(const gizmo_state_t *s){(void)s; return 2;}
const char *gizmo_kw_tag(const gizmo_state_t *s, size_t i){
    (void)s; return i==0?"F_C":"SEQ"; }
const gizmo_kw_t *gizmo_kw_entry(const gizmo_state_t *s, size_t i){
    (void)s;
    static double dv=1.23e6; static long long sv[3]={10,20,30};
    if(i==0){ strcpy(_KW[0].tag,"F_C"); _KW[0].type='D'; _KW[0].count=1;
        _KW[0].elem_size=8; _KW[0].value=(unsigned char*)&dv; return &_KW[0]; }
    strcpy(_KW[1].tag,"SEQ"); _KW[1].type='X'; _KW[1].count=3;
    _KW[1].elem_size=8; _KW[1].value=(unsigned char*)sv; return &_KW[1]; }
"""


def _skip_build() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


@pytest.mark.skipif(bool(_skip_build()), reason=_skip_build() or "")
def test_build_and_decode(tmp_path):
    d = tmp_path / "proj"
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    cfg = C.load(d)
    cfg["codec"] = {"kw": _CODEC}
    C.save(d, cfg)
    # add the property via the `jm property` path (materialises the getter into
    # the module fragment; a direct-cfg property on a module object is a
    # separate, pre-existing apply limitation).
    _q(
        property_run,
        d,
        "gizmo",
        "keywords",
        module="widget",
        ctype="dict",
        writable=False,
        codec="kw",
        count_fn="gizmo_kw_count",
        key_fn="gizmo_kw_tag",
        entry_fn="gizmo_kw_entry",
        entry_type="gizmo_kw_t",
        type_field="type",
        count_field="count",
        value_field="value",
    )
    # hand-write the struct + cursor (the user's contract; jm declares neither).
    h = d / "native/inc/gizmo/gizmo_core.h"
    ht = h.read_text()
    idx = ht.rfind("#endif")
    h.write_text(ht[:idx] + _STRUCT_AND_CURSOR + "\n" + ht[idx:])
    core = d / "native/src/gizmo/gizmo_core.c"
    core.write_text(core.read_text() + _CURSOR_IMPL)

    build = d / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(d),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]} failed:\n{r.stdout}\n{r.stderr}"

    script = (
        "from proj.widget import Gizmo\n"
        "kw = Gizmo(1.0).keywords\n"
        # 'D' count==1 -> scalar float; 'X' count==3 -> list[int]
        "assert kw == {'F_C': 1.23e6, 'SEQ': [10, 20, 30]}, kw\n"
        "print('OK')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(d / "src")}
    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(d),
        timeout=120,
    )
    assert r.returncode == 0 and "OK" in r.stdout, f"{r.stdout}\n{r.stderr}"
