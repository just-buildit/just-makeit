"""Slice 2 of the variant codec: the write-pack method (gh-554).

A `[[obj.methods]]` with `codec` + `sink_fn` and a `role="variant"` param packs
a Python value into a discriminant-tagged host buffer and calls the external
sink — jm generates the whole binding (parse, per-code pack, sink call,
rc->error) and a precise-union `.pyi`, so there is no hand marshaler. The build
test compiles the generated ext against a trivial sink and exercises it for
real; the unit tests pin the generated C / `.pyi` / round-trip without a
compiler.
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
from just_makeit._apply import run as apply_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

_CODEC = {
    "kw": {
        "discriminant": "char",
        "scalar_collapse": True,
        "entries": [
            {"code": "A", "ctype": "char", "bytes": True},
            {"code": "X", "ctype": "int64_t"},
            {"code": "D", "ctype": "double"},
        ],
    }
}

_METHOD = {
    "name": "add_kw",
    "codec": "kw",
    "sink_fn": "gizmo_add_kw",
    "params": [
        {"name": "tag", "type": "const char *"},
        {"name": "type", "type": "char", "role": "discriminant"},
        {"name": "value", "role": "variant"},
    ],
}


def _q(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _module_with_codec_method(tmp: Path) -> Path:
    d = tmp / "proj"
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
    cfg["codec"] = _CODEC
    cfg["gizmo"].setdefault("methods", []).append(dict(_METHOD))
    C.save(d, cfg)
    _q(apply_run, d)
    return d


# ── generator unit (no compiler) ──────────────────────────────────────────────


class TestRenderPack:
    def _render(self):
        return K.render_pack(
            "gizmo", "Gizmo", "GizmoObj", _METHOD, _CODEC["kw"], "    /*g*/\n"
        )

    def test_c_has_parse_switch_sink(self):
        body, pmd, pyi = self._render()
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "sCO"' in body
        assert "switch (_type)" in body
        assert "unsupported code" in body  # the default branch
        # bytes branch + numeric pack + the external sink, twice (str/buffer)
        assert "PyUnicode_AsUTF8AndSize" in body
        assert "gizmo_add_kw(self->handle, tag, _type, _s" in body
        assert "gizmo_add_kw(self->handle, tag, _type, _buf, _count)" in body
        assert "int64_t _v = (int64_t)_ll;" in body
        assert "double _v = (double)_d;" in body

    def test_pmd_and_pyi(self):
        _body, pmd, pyi = self._render()
        assert '"add_kw"' in pmd and "METH_VARARGS | METH_KEYWORDS" in pmd
        assert pyi == (
            "    def add_kw(self, tag: str, type: str,"
            " value: str | int | float | Sequence[int] | Sequence[float])"
            " -> None:\n"
            '        """Add kw."""\n'
        )

    def test_missing_sink_or_role_rejected(self):
        with pytest.raises(K.CodecError, match="sink_fn"):
            K.validate_codec_method(
                "g", {"name": "m", "params": []}, _CODEC["kw"]
            )
        with pytest.raises(K.CodecError, match="discriminant"):
            K.validate_codec_method(
                "g",
                {
                    "name": "m",
                    "sink_fn": "s",
                    "params": [{"name": "v", "role": "variant"}],
                },
                _CODEC["kw"],
            )


# ── apply codegen + round-trip (no compiler) ──────────────────────────────────


class TestApplyCodegen:
    def test_ext_and_module_pyi(self, tmp_path):
        d = _module_with_codec_method(tmp_path)
        ext = (d / "native/src/widget/widget_ext_gizmo.c").read_text()
        assert "switch (_type)" in ext
        assert "gizmo_add_kw(self->handle" in ext
        assert '"add_kw"' in ext
        pyi = (d / "src/proj/widget/widget.pyi").read_text()
        assert (
            "def add_kw(self, tag: str, type: str, value: "
            "str | int | float | Sequence[int] | Sequence[float]) -> None:"
            in pyi
        )
        # the Sequence union must import its name (else an undefined-name stub)
        assert "from collections.abc import Sequence" in pyi

    def test_no_stray_core_stub(self, tmp_path):
        # a codec method has no C core fn — jm must not scaffold one.
        d = _module_with_codec_method(tmp_path)
        core_h = (d / "native/inc/gizmo/gizmo_core.h").read_text()
        assert "gizmo_add_kw" not in core_h

    def test_roundtrip_preserves_codec_keys(self, tmp_path):
        d = _module_with_codec_method(tmp_path)
        cfg = C.load(d)
        m = next(x for x in cfg["gizmo"]["methods"] if x["name"] == "add_kw")
        assert m["codec"] == "kw" and m["sink_fn"] == "gizmo_add_kw"
        roles = [
            (p["name"], p.get("role"), p.get("type")) for p in m["params"]
        ]
        assert roles == [
            ("tag", None, "const char *"),
            ("type", "discriminant", "char"),
            ("value", "variant", None),
        ]


# ── build + run (needs a compiler) ────────────────────────────────────────────


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
def test_build_and_call(tmp_path):
    d = _module_with_codec_method(tmp_path)
    # hand-write the pure-C sink: declared in the sacred header, implemented in
    # the sacred core. It rejects a too-long buffer so rc->ValueError is live.
    h = d / "native/inc/gizmo/gizmo_core.h"
    ht = h.read_text()
    idx = ht.rfind("#endif")
    decl = (
        "int gizmo_add_kw(gizmo_state_t *s, const char *tag, char type,\n"
        "                 const void *val, size_t count);\n"
    )
    h.write_text(ht[:idx] + decl + "\n" + ht[idx:])
    core = d / "native/src/gizmo/gizmo_core.c"
    core.write_text(
        core.read_text()
        + "\n#include <string.h>\n"
        + "int gizmo_add_kw(gizmo_state_t *s, const char *tag, char type,\n"
        + "                 const void *val, size_t count)\n"
        + "{ (void)s; (void)tag; (void)type; (void)val;\n"
        + "  return count > 100 ? -1 : 0; }\n"
    )
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
        "g = Gizmo(2.0)\n"
        "g.add_kw('F_C', 'D', 1.23e6)\n"  # scalar double
        "g.add_kw('NOTE', 'A', 'hello')\n"  # str -> bytes
        "g.add_kw('SEQ', 'X', [1, 2, 3])\n"  # sequence of int64
        "raised = False\n"
        "try:\n"
        "    g.add_kw('BAD', 'Z', 1)\n"  # unknown code -> ValueError
        "except ValueError:\n"
        "    raised = True\n"
        "assert raised, 'unknown code should raise'\n"
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
