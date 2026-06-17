"""Unit tests for the capsule module schema + readers (gh-286).

A ``kind = "capsule"`` module declares free functions over an opaque PyCapsule
state — ``<backing>_create`` / ``execute`` / ``reset`` / ``destroy`` /
``get_*`` / ``set_*`` — instead of a ``PyTypeObject`` per object. This covers
the manifest shape, the config readers, and the save/load round-trip; the
codegen lands in a later slice."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    capsule_backing,
    capsule_name,
    is_capsule_module,
    load,
    module_init_params,
    module_kind,
    module_methods,
    module_properties,
    save,
)


def _cfg():
    return {
        "project": {"name": "doppler", "version": "0.1.0"},
        "module": {
            "ddc_fn": {
                "kind": "capsule",
                "backing": "ddcr",
                "capsule_name": "doppler.ddc.ddcr_state",
                "init_params": [
                    {"name": "norm_freq", "type": "double"},
                    {"name": "rate", "type": "double"},
                ],
                "methods": [
                    {
                        "name": "execute",
                        "arg_type": "float _Complex[]",
                        "return_type": "float _Complex[]",
                        "caller_out": True,
                        "nogil": True,
                    },
                    {"name": "reset"},
                ],
                "properties": [
                    {"name": "norm_freq", "type": "double", "writable": True},
                    {"name": "rate", "type": "double"},
                ],
            }
        },
    }


class TestReaders:
    def test_kind_and_capsule_flag(self):
        cfg = _cfg()
        assert module_kind(cfg, "ddc_fn") == "capsule"
        assert is_capsule_module(cfg, "ddc_fn")

    def test_non_capsule_module(self):
        cfg = {"module": {"dsp": {"objects": ["fir"]}}}
        assert module_kind(cfg, "dsp") is None
        assert not is_capsule_module(cfg, "dsp")

    def test_backing_and_capsule_name(self):
        cfg = _cfg()
        assert capsule_backing(cfg, "ddc_fn") == "ddcr"
        assert capsule_name(cfg, "ddc_fn") == "doppler.ddc.ddcr_state"

    def test_init_params(self):
        assert module_init_params(_cfg(), "ddc_fn") == [
            ("norm_freq", "double", ""),
            ("rate", "double", ""),
        ]

    def test_methods(self):
        methods = module_methods(_cfg(), "ddc_fn")
        assert [m["name"] for m in methods] == ["execute", "reset"]
        execute = methods[0]
        assert execute["caller_out"] is True
        assert execute["nogil"] is True
        assert execute["arg_type"] == "float _Complex[]"
        # reset is a bare method (no I/O, no gil release).
        assert "caller_out" not in methods[1]

    def test_properties(self):
        props = module_properties(_cfg(), "ddc_fn")
        assert [(p["name"], p.get("writable", False)) for p in props] == [
            ("norm_freq", True),  # -> get + set
            ("rate", False),  # -> get only
        ]


class TestRoundTrip:
    def test_save_load_preserves_capsule_module(self, tmp_path):
        save(tmp_path, _cfg())
        manifest = (tmp_path / "just-makeit.toml").read_text()
        # The capsule keys render; no spurious `objects = []`.
        assert 'kind = "capsule"' in manifest
        assert 'backing = "ddcr"' in manifest
        assert "[[module.ddc_fn.methods]]" in manifest
        assert "[[module.ddc_fn.properties]]" in manifest
        assert "objects" not in manifest

        cfg2 = load(tmp_path)
        assert is_capsule_module(cfg2, "ddc_fn")
        assert capsule_backing(cfg2, "ddc_fn") == "ddcr"
        assert [m["name"] for m in module_methods(cfg2, "ddc_fn")] == [
            "execute",
            "reset",
        ]
        assert module_methods(cfg2, "ddc_fn")[0]["nogil"] is True
        assert [
            (p["name"], p.get("writable", False))
            for p in module_properties(cfg2, "ddc_fn")
        ] == [("norm_freq", True), ("rate", False)]
        # The module dict carries no `objects` key.
        assert "objects" not in cfg2["module"]["ddc_fn"]
