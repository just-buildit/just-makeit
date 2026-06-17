"""Unit tests for the handle module schema + readers (gh-306).

A ``kind = "handle"`` module declares one typed CPython class over an OPAQUE
hand-C resource handle — the intersection of the capsule (opaque backing) and
composer (typed class) generators. This covers the manifest shape, the config
readers, and the save/load round-trip; the codegen lives in
``test_handle_codegen.py``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    handle_backing,
    handle_close_fn,
    handle_context,
    handle_create_args,
    handle_create_fn,
    handle_create_post,
    handle_getters,
    handle_methods,
    handle_optional_backend,
    handle_type,
    handle_type_name,
    is_handle_module,
    load,
    module_kind,
    save,
)


def _cfg():
    """A wfm-like ``Writer`` handle (the doppler archetype)."""
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "enum": [
            {"name": "ftype", "values": ["raw", "csv"]},
            {"name": "stype", "values": ["cf32", "cf64", "ci16"]},
        ],
        "module": {
            "wfm_writer": {
                "kind": "handle",
                "backing": "wfm_writer",
                "package": "wfm",
                "header": "wfm/wfm_writer.h",
                "type_name": "Writer",
                "context_manager": True,
                "close_fn": "wfm_writer_close",
                "create_fn": "wfm_writer_open",
                "depends_on": [{"name": "wfm_writer", "link": True}],
                "extra_link_libs": ["m"],
                "create_args": [
                    {"name": "path", "type": "path"},
                    {
                        "name": "file_type",
                        "type": "int",
                        "enum": "ftype",
                        "default": "raw",
                        "kwonly": True,
                    },
                    {
                        "name": "sample_type",
                        "type": "int",
                        "enum": "stype",
                        "default": "cf32",
                        "kwonly": True,
                    },
                    {"name": "headroom", "type": "double", "default": "0.0"},
                ],
                "create_post": [
                    {
                        "fn": "wfm_writer_set_gain",
                        "when": "headroom",
                        "arg": "pow(10, -headroom/20)",
                    }
                ],
                "methods": [
                    {
                        "name": "write",
                        "fn": "wfm_writer_write",
                        "returns": "size_t",
                        "nogil": True,
                        "args": [{"name": "iq", "type": "float _Complex[]"}],
                    }
                ],
                "getters": [
                    {
                        "fn": "wfm_writer_stats",
                        "out": "wfm_writer_stats_t",
                        "cache": False,
                        "fields": [
                            {
                                "name": "clip_fraction",
                                "from": "frac",
                                "type": "double",
                            },
                            {
                                "name": "clipped",
                                "type": "bool",
                                "expr": "self->sample_type >= 2 "
                                "&& tmp.peak > 1.0",
                            },
                        ],
                    }
                ],
            }
        },
    }


class TestReaders:
    def test_kind_and_handle_flag(self):
        cfg = _cfg()
        assert module_kind(cfg, "wfm_writer") == "handle"
        assert is_handle_module(cfg, "wfm_writer")

    def test_non_handle_module(self):
        cfg = {"module": {"dsp": {"objects": ["fir"]}}}
        assert module_kind(cfg, "dsp") is None
        assert not is_handle_module(cfg, "dsp")

    def test_backing_type_and_names(self):
        cfg = _cfg()
        assert handle_backing(cfg, "wfm_writer") == "wfm_writer"
        assert handle_type(cfg, "wfm_writer") == "wfm_writer_t"
        assert handle_type_name(cfg, "wfm_writer") == "Writer"
        assert handle_create_fn(cfg, "wfm_writer") == "wfm_writer_open"
        assert handle_close_fn(cfg, "wfm_writer") == "wfm_writer_close"

    def test_defaults_when_unset(self):
        cfg = {"module": {"h": {"kind": "handle", "backing": "ring"}}}
        # handle_type / create_fn / close_fn / type_name all derive from backing
        assert handle_type(cfg, "h") == "ring_t"
        assert handle_create_fn(cfg, "h") == "ring_open"
        assert handle_close_fn(cfg, "h") == "ring_close"
        assert handle_type_name(cfg, "h") == "Ring"

    def test_create_args(self):
        args = handle_create_args(_cfg(), "wfm_writer")
        assert [a["name"] for a in args] == [
            "path",
            "file_type",
            "sample_type",
            "headroom",
        ]
        assert args[0]["type"] == "path"
        assert args[1]["enum"] == "ftype"

    def test_create_post(self):
        post = handle_create_post(_cfg(), "wfm_writer")
        assert post[0]["fn"] == "wfm_writer_set_gain"
        assert post[0]["when"] == "headroom"

    def test_methods(self):
        methods = handle_methods(_cfg(), "wfm_writer")
        assert methods[0]["name"] == "write"
        assert methods[0]["returns"] == "size_t"
        assert methods[0]["nogil"] is True
        assert methods[0]["args"][0]["type"] == "float _Complex[]"

    def test_getters(self):
        getters = handle_getters(_cfg(), "wfm_writer")
        assert getters[0]["out"] == "wfm_writer_stats_t"
        fields = getters[0]["fields"]
        assert fields[0]["from"] == "frac"
        assert "expr" in fields[1]

    def test_context_and_backend(self):
        assert handle_context(_cfg(), "wfm_writer") is True
        assert handle_optional_backend(_cfg(), "wfm_writer") == ""


class TestRoundTrip:
    def test_save_load_preserves_handle_module(self, tmp_path):
        save(tmp_path, _cfg())
        manifest = (tmp_path / "just-makeit.toml").read_text()
        assert 'kind = "handle"' in manifest
        assert 'backing = "wfm_writer"' in manifest
        assert "[[module.wfm_writer.create_args]]" in manifest
        assert "[[module.wfm_writer.methods]]" in manifest
        assert "[[module.wfm_writer.getters]]" in manifest
        # A handle module carries no `objects` key.
        assert "objects" not in manifest

        cfg2 = load(tmp_path)
        assert is_handle_module(cfg2, "wfm_writer")
        assert handle_create_fn(cfg2, "wfm_writer") == "wfm_writer_open"
        assert [m["name"] for m in handle_methods(cfg2, "wfm_writer")] == [
            "write"
        ]
        assert handle_getters(cfg2, "wfm_writer")[0]["out"] == (
            "wfm_writer_stats_t"
        )
        assert "objects" not in cfg2["module"]["wfm_writer"]
