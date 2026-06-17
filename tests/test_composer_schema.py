"""Unit tests for the composer module schema + readers (gh-287).

A ``kind = "composer"`` module is built on the capsule skeleton (gh-286) but
adds a multi-source / segment / timeline composition model plus CPython OO
types. This covers the manifest shape (``composes`` + the
``source``/``segment``/``timeline``/``oo``/``json`` sub-tables), the config
readers, and the save/load round-trip. The codegen lands in a later slice.

The fixture mirrors doppler's ``wfm_compose`` (wfm_compose.h) — a composer over
the ``wfm_synth`` generator object."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    capsule_backing,
    capsule_name,
    composer_composes,
    composer_json,
    composer_oo,
    composer_sample_type,
    composer_segment,
    composer_source,
    composer_stream,
    composer_timeline,
    is_capsule_module,
    is_composer_module,
    load,
    module_kind,
    save,
)


def _cfg():
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "enum": [
            {
                "name": "wfm_type",
                "values": ["tone", "noise", "pn", "bpsk", "qpsk"],
            },
            {"name": "snr_mode", "values": ["auto", "fs", "ebno", "esno"]},
        ],
        "module": {
            "wfm_compose": {
                "kind": "composer",
                "backing": "wfm_compose",
                "capsule_name": "doppler.wfm.compose_state",
                "package": "wfm",
                "composes": ["wfm_synth"],
                "sample_type": True,
                "depends_on": [{"name": "wfm", "link": True}],
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
                        {
                            "name": "snr_mode",
                            "type": "int",
                            "enum": "snr_mode",
                            "default": "auto",
                        },
                        {"name": "level", "type": "double", "default": "0.0"},
                        {"name": "bits", "type": "uint8_t*", "bytes": True},
                    ],
                },
                "segment": {
                    "type_name": "Segment",
                    "struct": "wfm_segment_t",
                    "fields": [
                        {"name": "fs", "type": "double"},
                        {"name": "num_samples", "type": "size_t"},
                        {
                            "name": "off_samples",
                            "type": "size_t",
                            "default": "0",
                        },
                    ],
                    "sources": "multi",
                },
                "timeline": {
                    "type_name": "Timeline",
                    "loop": ["once", "repeat", "continuous"],
                },
                "oo": {
                    "factories": ["tone", "noise", "pn", "bpsk", "qpsk"],
                    "emit": "ctypes",
                    "discriminant": "type",
                    "composer_type_name": "Composer",
                },
                "json": {
                    "enabled": True,
                    "to_json_fn": "wfm_spec_to_json",
                    "to_json_trailing": ["0.0"],
                },
            }
        },
    }


class TestReaders:
    def test_kind_and_flags(self):
        cfg = _cfg()
        assert module_kind(cfg, "wfm_compose") == "composer"
        assert is_composer_module(cfg, "wfm_compose")
        # a composer is NOT a capsule (different generator), though it shares
        # the backing/capsule_name keys.
        assert not is_capsule_module(cfg, "wfm_compose")

    def test_shared_capsule_keys(self):
        cfg = _cfg()
        assert capsule_backing(cfg, "wfm_compose") == "wfm_compose"
        assert capsule_name(cfg, "wfm_compose") == "doppler.wfm.compose_state"

    def test_composes_and_sample_type(self):
        cfg = _cfg()
        assert composer_composes(cfg, "wfm_compose") == ["wfm_synth"]
        assert composer_sample_type(cfg, "wfm_compose") is True

    def test_source_table(self):
        src = composer_source(_cfg(), "wfm_compose")
        assert src["object"] == "wfm_synth"
        assert src["struct"] == "wfm_source_t"
        assert src["type_name"] == "Synth"
        assert [f["name"] for f in src["fields"]] == [
            "type",
            "snr_mode",
            "level",
            "bits",
        ]
        # enum + bytes tags survive on the fields
        assert src["fields"][0]["enum"] == "wfm_type"
        assert src["fields"][3]["bytes"] is True

    def test_segment_table(self):
        seg = composer_segment(_cfg(), "wfm_compose")
        assert seg["sources"] == "multi"
        assert [f["name"] for f in seg["fields"]] == [
            "fs",
            "num_samples",
            "off_samples",
        ]
        assert seg["fields"][2]["default"] == "0"

    def test_timeline_table(self):
        assert composer_timeline(_cfg(), "wfm_compose")["loop"] == [
            "once",
            "repeat",
            "continuous",
        ]

    def test_oo_and_json(self):
        cfg = _cfg()
        oo = composer_oo(cfg, "wfm_compose")
        assert oo["emit"] == "ctypes"
        assert oo["factories"][0] == "tone"
        assert composer_json(cfg, "wfm_compose") is True

    def test_defaults_when_absent(self):
        cfg = {"module": {"m": {"kind": "composer"}}}
        assert composer_composes(cfg, "m") == []
        assert composer_sample_type(cfg, "m") is False
        assert composer_source(cfg, "m") == {}
        assert composer_segment(cfg, "m") == {}
        assert composer_timeline(cfg, "m") == {}
        assert composer_oo(cfg, "m") == {}
        assert composer_json(cfg, "m") is False


class TestRoundTrip:
    def test_save_load_preserves_composer_module(self, tmp_path):
        save(tmp_path, _cfg())
        manifest = (tmp_path / "just-makeit.toml").read_text()
        assert 'kind = "composer"' in manifest
        assert 'composes = ["wfm_synth"]' in manifest
        assert "sample_type = true" in manifest
        assert "[module.wfm_compose.source]" in manifest
        assert "[module.wfm_compose.segment]" in manifest
        assert "[module.wfm_compose.timeline]" in manifest
        assert "[module.wfm_compose.oo]" in manifest
        assert "[module.wfm_compose.json]" in manifest
        # no spurious object-group list
        assert "objects" not in manifest

        cfg2 = load(tmp_path)
        assert is_composer_module(cfg2, "wfm_compose")
        assert composer_composes(cfg2, "wfm_compose") == ["wfm_synth"]
        assert composer_sample_type(cfg2, "wfm_compose") is True
        # the full sub-tables survive structurally
        assert composer_source(cfg2, "wfm_compose") == composer_source(
            _cfg(), "wfm_compose"
        )
        assert composer_segment(cfg2, "wfm_compose") == composer_segment(
            _cfg(), "wfm_compose"
        )
        assert composer_timeline(cfg2, "wfm_compose") == composer_timeline(
            _cfg(), "wfm_compose"
        )
        assert composer_oo(cfg2, "wfm_compose") == composer_oo(
            _cfg(), "wfm_compose"
        )
        assert composer_json(cfg2, "wfm_compose") is True
        # the [[enum]] SSOT (gh-285) the composer reads still round-trips
        assert {e["name"] for e in cfg2.get("enum", [])} == {
            "wfm_type",
            "snr_mode",
        }


def test_save_load_preserves_source_generates(tmp_path):
    """``[module.X.source.generates]`` (feature 1) survives save/load."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["source"]["generates"] = {
        "generator": "wfm_synth",
        "bridge_fn": "wfm_source_to_synth",
    }
    save(tmp_path, cfg)
    manifest = (tmp_path / "just-makeit.toml").read_text()
    assert "[module.wfm_compose.source.generates]" in manifest
    assert 'generator = "wfm_synth"' in manifest
    assert 'bridge_fn = "wfm_source_to_synth"' in manifest

    cfg2 = load(tmp_path)
    assert composer_source(cfg2, "wfm_compose")["generates"] == {
        "generator": "wfm_synth",
        "bridge_fn": "wfm_source_to_synth",
    }


def test_save_load_preserves_aliases_and_coerce(tmp_path):
    """Field ``aliases`` / ``coerce`` (feature 2) survive save/load."""
    cfg = _cfg()
    for f in cfg["module"]["wfm_compose"]["source"]["fields"]:
        if f["name"] == "level":
            f["aliases"] = ["amplitude"]
        if f["name"] == "bits":
            f["aliases"] = ["pattern"]
            f["coerce"] = "bit_pattern"
    save(tmp_path, cfg)
    cfg2 = load(tmp_path)
    by = {f["name"]: f for f in composer_source(cfg2, "wfm_compose")["fields"]}
    assert by["level"]["aliases"] == ["amplitude"]
    assert by["bits"]["aliases"] == ["pattern"]
    assert by["bits"]["coerce"] == "bit_pattern"


def test_save_load_preserves_composer_stream(tmp_path):
    """``[module.X.composer] stream`` (feature 3) survives save/load."""
    cfg = _cfg()
    cfg["module"]["wfm_compose"]["composer"] = {"stream": True}
    save(tmp_path, cfg)
    manifest = (tmp_path / "just-makeit.toml").read_text()
    assert "[module.wfm_compose.composer]" in manifest
    assert "stream = true" in manifest

    cfg2 = load(tmp_path)
    assert composer_stream(cfg2, "wfm_compose").get("stream") is True


def test_composer_stream_absent_by_default():
    """No ``[module.X.composer]`` table → ``composer_stream`` is empty."""
    assert composer_stream(_cfg(), "wfm_compose") == {}
