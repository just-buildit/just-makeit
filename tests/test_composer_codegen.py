"""Codegen tests for ``kind = "composer"`` modules (gh-287, C2.2.a).

Render the enum tables and the source ``PyTypeObject`` (e.g. ``Synth``) for a
composer module and assert the generated C has the right shape. The full
compile + behavior parity is exercised against doppler's real ``wfm_source_t``
in the pilot; this keeps the structure under a compiler-free unit gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _composer


def _cfg():
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "enum": [
            {
                "name": "wfm_type",
                "values": ["tone", "noise", "pn", "bpsk", "qpsk"],
            },
            {"name": "snr_mode", "values": ["auto", "fs", "ebno", "esno"]},
            {"name": "lfsr", "values": ["galois", "fibonacci"]},
        ],
        "module": {
            "wfm_compose": {
                "kind": "composer",
                "backing": "wfm_compose",
                "capsule_name": "doppler.wfm.compose_state",
                "package": "wfm",
                "composes": ["wfm_synth"],
                "source": {
                    "object": "wfm_synth",
                    "struct": "wfm_source_t",
                    "type_name": "Synth",
                    "fields": [
                        {
                            "name": "type",
                            "type": "int",
                            "enum": "wfm_type",
                            "default": "tone",
                        },
                        {"name": "freq", "type": "double", "default": "0.0"},
                        {
                            "name": "snr_mode",
                            "type": "int",
                            "enum": "snr_mode",
                            "default": "auto",
                        },
                        {"name": "seed", "type": "uint32_t", "default": "1"},
                        {
                            "name": "pn_poly",
                            "type": "uint64_t",
                            "default": "0",
                        },
                        {
                            "name": "lfsr",
                            "type": "int",
                            "enum": "lfsr",
                            "default": "galois",
                        },
                        {"name": "bits", "type": "uint8_t*", "bytes": True},
                    ],
                },
                "segment": {
                    "fields": [
                        {"name": "fs", "type": "double"},
                        {
                            "name": "snr_mode",
                            "type": "int",
                            "enum": "snr_mode",
                        },
                    ],
                    "sources": "multi",
                },
                "oo": {
                    "factories": ["tone", "qpsk"],
                    "emit": "ctypes",
                    "discriminant": "type",
                },
            }
        },
    }


class TestEnumTables:
    def test_only_referenced_enums_emitted(self):
        s = _composer.render_enum_tables(_cfg(), "wfm_compose")
        # wfm_type / snr_mode / lfsr are referenced; the table order is the SSOT
        assert "static const char *const _enum_wfm_type[] = {" in s
        assert "static const char *const _enum_snr_mode[] = {" in s
        assert "static const char *const _enum_lfsr[] = {" in s
        assert "_enum_index(const char *const *tab, const char *s)" in s
        # values in declared order, NULL-terminated
        idx_tone = s.index('"tone"')
        idx_qpsk = s.index('"qpsk"')
        assert idx_tone < idx_qpsk

    def test_unreferenced_enum_skipped(self):
        cfg = _cfg()
        cfg["enum"].append({"name": "unused", "values": ["x", "y"]})
        s = _composer.render_enum_tables(cfg, "wfm_compose")
        assert "_enum_unused" not in s


class TestSourceType:
    def test_struct_and_type_object(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "wfm_source_t src;" in s
        assert "double   fs;" in s
        assert "static PyTypeObject SynthType =" in s
        assert '.tp_name      = "doppler.wfm.Synth"' in s
        assert "(initproc)Synth_init" in s
        assert "(destructor)Synth_dealloc" in s

    def test_init_keyword_parse(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        # kwlist has every field + fs
        assert (
            '"type", "freq", "snr_mode", "seed", "pn_poly", "lfsr", "bits", "fs"'
            in s
        )
        # enum field validated into the struct int
        assert "_enum_index(_enum_wfm_type, type)" in s
        assert "self->src.type = _i;" in s
        # scalar assigned directly
        assert "self->src.freq = freq;" in s
        # bytes attached
        assert "_attach_bytes(&self->src, bits)" in s

    def test_getset_enum_as_string(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "PyUnicode_FromString(_enum_wfm_type[self->src.type])" in s
        # writable enum setter validates
        assert "Synth_set_snr_mode" in s
        # scalar getter coerces
        assert "PyLong_FromUnsignedLongLong" in s  # pn_poly (uint64)

    def test_dealloc_frees_bits(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert "free(self->src.bits);" in s

    def test_factories(self):
        s = _composer.render_source_type(_cfg(), "wfm_compose")
        assert 'return _Synth_factory("tone", args, kwds);' in s
        assert 'return _Synth_factory("qpsk", args, kwds);' in s
        # the discriminant is injected
        assert 'PyDict_SetItemString(k, "type", v)' in s

    def test_factory_method_rows(self):
        rows = _composer.factory_method_rows(_cfg(), "wfm_compose")
        joined = "\n".join(rows)
        assert '{"tone", (PyCFunction)_factory_tone' in joined
        assert "METH_VARARGS | METH_KEYWORDS" in joined
