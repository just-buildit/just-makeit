"""gh-432 — capsule-typed method params + status-return methods.

A method param may declare `capsule = "<name>"`: its C type is a foreign
pointer (e.g. `dp_tlm_t *`) that crosses the Python boundary as a named
PyCapsule. The generated glue parses the arg as a raw object and unwraps
it after ParseTuple: None -> NULL (the C-side detach idiom), a capsule ->
PyCapsule_GetPointer with the declared name, and any other object -> its
`_capsule` attribute first (so callers pass the friendly wrapper, not the
capsule). The param's optional `header = "path/hdr.h"` names the foreign
type's header, injected into the component's _core.h alongside the
gh-170 depends_on includes.

A method may also declare `status_return = true`: its C `int` return is a
status (0 = OK), bound as `-> None` raising ValueError on failure — the
same contract the serializable set_state glue emits.

The motivating consumer is doppler's telemetry attach face
(doppler-dsp/doppler#378):

    int agc_set_telemetry(agc_state_t *s, dp_tlm_t *t,
                          const char *prefix, uint32_t decim);
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from conftest import flatten_signatures  # noqa: E402

from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._object import run as object_run
from just_makeit._apply import run as apply_run
from just_makeit import _config as C

CAPSULE_NAME = "dsp.telemetry.dp_tlm"

SET_TELEMETRY = {
    "name": "set_telemetry",
    "arg_type": "void",
    "return_type": "int",
    "status_return": True,
    "params": [
        {
            "name": "tlm",
            "type": "dp_tlm_t *",
            "capsule": CAPSULE_NAME,
            "header": "telemetry/telemetry.h",
        },
        {"name": "prefix", "type": "const char *"},
        {"name": "decim", "type": "uint32_t", "default": "1"},
    ],
}


def _foreign_header(dest: Path) -> None:
    """Plant the foreign type's header under native/inc (out-of-convention
    location: telemetry/telemetry.h, not telemetry/telemetry_core.h)."""
    hdr = dest / "native" / "inc" / "telemetry" / "telemetry.h"
    hdr.parent.mkdir(parents=True, exist_ok=True)
    hdr.write_text(
        "#ifndef TLM_H\n#define TLM_H\n"
        "typedef struct dp_tlm dp_tlm_t;\n"
        "#endif\n",
        encoding="utf-8",
    )


def _scaffold_standalone(tmp_path: Path) -> Path:
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    object_run(dest, "agc", module=None)
    _foreign_header(dest)
    cfg = C.load(dest)
    C.add_method(cfg, "agc", dict(SET_TELEMETRY))
    C.save(dest, cfg)
    apply_run(dest)
    return dest


def _scaffold_module(tmp_path: Path) -> Path:
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    module_run(dest, "track")
    object_run(dest, "agc", "track")
    _foreign_header(dest)
    cfg = C.load(dest)
    C.add_method(cfg, "agc", dict(SET_TELEMETRY))
    C.save(dest, cfg)
    apply_run(dest)
    return dest


class TestGeneratedParseGlue:
    def test_capsule_unwrap_in_ext_c(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        ext = (dest / "native" / "src" / "agc" / "agc_ext.c").read_text(
            encoding="utf-8"
        )
        # Raw-object parse, None -> NULL, name-checked GetPointer, and the
        # `_capsule` duck-typed unwrap for wrapper objects.
        assert "PyObject *tlm_obj = Py_None;" in ext
        assert "dp_tlm_t *tlm = NULL;" in ext
        assert "if (tlm_obj != Py_None) {" in ext
        assert "PyCapsule_CheckExact(tlm_cap)" in ext
        assert '"_capsule"' in ext
        assert f'"{CAPSULE_NAME}"' in ext
        assert "PyCapsule_GetPointer" in ext
        # The unwrapped pointer (not the PyObject) reaches the C call.
        assert "agc_set_telemetry(self->handle, tlm, prefix, decim)" in ext

    def test_status_return_raises_on_nonzero(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        ext = (dest / "native" / "src" / "agc" / "agc_ext.c").read_text(
            encoding="utf-8"
        )
        assert (
            "int _rc = agc_set_telemetry(self->handle, tlm, prefix, decim);"
            in ext
        )
        assert "if (_rc != 0) {" in ext
        assert "PyErr_Format(PyExc_ValueError" in ext
        assert '"set_telemetry failed (rc=%d)"' in ext
        assert "Py_RETURN_NONE;" in ext

    def test_sibling_params_still_parse(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        ext = (dest / "native" / "src" / "agc" / "agc_ext.c").read_text(
            encoding="utf-8"
        )
        # const char * prefix parses as "s"; the defaulted uint32_t goes
        # through the parse_type path (unsigned long + "k") after `|`, and
        # its raw local seeds from the gh-240 default (drive-by fix: it
        # previously seeded parse_zero, so an omitted arg yielded 0).
        assert '"Os|k"' in ext
        assert "const char * prefix = NULL;" in ext
        assert "unsigned long decim_raw = 1;" in ext
        assert "uint32_t decim = (uint32_t)decim_raw;" in ext


class TestHeaderAndPrototype:
    def test_core_h_gains_include_and_decl(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        core_h = (dest / "native" / "inc" / "agc" / "agc_core.h").read_text(
            encoding="utf-8"
        )
        assert '#include "telemetry/telemetry.h"' in core_h
        assert (
            "int agc_set_telemetry(agc_state_t *state,"
            " dp_tlm_t * tlm, const char * prefix, uint32_t decim);" in core_h
        )

    def test_module_object_core_h_gains_include(self, tmp_path):
        dest = _scaffold_module(tmp_path)
        core_h = (dest / "native" / "inc" / "agc" / "agc_core.h").read_text(
            encoding="utf-8"
        )
        assert '#include "telemetry/telemetry.h"' in core_h

    def test_missing_header_is_not_injected(self, tmp_path):
        # A header key naming a file that doesn't exist under native/inc is
        # skipped (mirrors the gh-170 link-target rule) — no broken include.
        dest = tmp_path / "dsp"
        new_run("dsp", dest)
        object_run(dest, "agc", module=None)
        m = dict(SET_TELEMETRY)
        m["params"] = [dict(m["params"][0], header="nope/nope.h")] + [
            dict(p) for p in m["params"][1:]
        ]
        cfg = C.load(dest)
        C.add_method(cfg, "agc", m)
        C.save(dest, cfg)
        apply_run(dest)
        core_h = (dest / "native" / "inc" / "agc" / "agc_core.h").read_text(
            encoding="utf-8"
        )
        assert "nope/nope.h" not in core_h


class TestPyiStubs:
    def test_standalone_pyi(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        pyi = flatten_signatures(
            (dest / "src" / "dsp" / "agc.pyi").read_text(encoding="utf-8")
        )
        assert (
            "def set_telemetry(self, tlm: object | None, prefix: str,"
            " decim: int = 1) -> None:" in pyi
        )

    def test_module_pyi(self, tmp_path):
        # _stubs.py::make_module_pyi is a separate generator (gh-423) — it
        # must map the capsule param and the status return identically.
        # gh-450: it kept its own _CTYPE_TO_PY table without a "const
        # char *" entry, so `prefix` fell through to "Any" here even
        # though the standalone generator (_context/_methods.py) got it
        # right — assert the full signature, not just the capsule param,
        # so a missing scalar-type mapping can't hide again.
        dest = _scaffold_module(tmp_path)
        pyi = flatten_signatures(
            (dest / "src" / "dsp" / "track" / "track.pyi").read_text(
                encoding="utf-8"
            )
        )
        assert (
            "def set_telemetry(self, tlm: object | None, prefix: str,"
            " decim: int = 1) -> None:" in pyi
        )


class TestRoundTrip:
    def test_capsule_keys_survive_save_load(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        cfg = C.load(dest)
        (m,) = [
            m for m in C.methods(cfg, "agc") if m["name"] == "set_telemetry"
        ]
        assert m["status_return"] is True
        assert m["params"][0]["capsule"] == CAPSULE_NAME
        assert m["params"][0]["header"] == "telemetry/telemetry.h"
        assert m["params"][2]["default"] == "1"

    def test_save_is_idempotent(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        toml_path = dest / "just-makeit.toml"
        first = toml_path.read_text(encoding="utf-8")
        C.save(dest, C.load(dest))
        assert toml_path.read_text(encoding="utf-8") == first

    def test_apply_is_idempotent(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        ext_path = dest / "native" / "src" / "agc" / "agc_ext.c"
        core_h_path = dest / "native" / "inc" / "agc" / "agc_core.h"
        ext_before = ext_path.read_text(encoding="utf-8")
        core_before = core_h_path.read_text(encoding="utf-8")
        apply_run(dest)
        assert ext_path.read_text(encoding="utf-8") == ext_before
        assert core_h_path.read_text(encoding="utf-8") == core_before

    def test_param_headers_accessor(self, tmp_path):
        dest = _scaffold_standalone(tmp_path)
        cfg = C.load(dest)
        assert C.param_headers(cfg, "agc") == ["telemetry/telemetry.h"]
        assert C.param_headers(cfg, "missing") == []
