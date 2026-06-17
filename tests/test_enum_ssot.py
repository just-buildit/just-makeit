"""Unit tests for the ``[[enum]]`` single-source-of-truth (gh-285).

A named top-level ``[[enum]]`` declares an ordered value set once; a parameter
refers to it with ``type = "enum:<name>"``. The config layer resolves the
reference to the equivalent ``string_enum:`` spec on the codegen read path, so
every existing consumer is unchanged, while the manifest keeps the reference on
disk (no per-parameter duplication of the value list)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    enums,
    init_params,
    load,
    resolve_enum_type,
    save,
)
from just_makeit._types import (
    enum_ref_name,
    is_enum_ref,
    is_string_enum_type,
    string_enum_choices,
)


def _cfg():
    return {
        "project": {"name": "p", "version": "0.1.0"},
        "enum": [
            {"name": "wfm_type", "values": ["tone", "noise", "pn", "qpsk"]},
            {"name": "snr_mode", "values": ["auto", "fs", "ebno"]},
        ],
        "syn": {
            "arg_type": "void",
            "init_params": [
                {"name": "kind", "type": "enum:wfm_type", "default": "tone"},
                {"name": "mode", "type": "enum:snr_mode", "default": "auto"},
                {"name": "fs", "type": "double", "default": "1e6"},
            ],
        },
    }


class TestTypeHelpers:
    def test_is_enum_ref(self):
        assert is_enum_ref("enum:wfm_type")
        assert not is_enum_ref("string_enum:a,b")
        assert not is_enum_ref("double")

    def test_enum_ref_name(self):
        assert enum_ref_name("enum:wfm_type") == "wfm_type"


class TestEnumsReader:
    def test_enums_maps_name_to_ordered_values(self):
        assert enums(_cfg()) == {
            "wfm_type": ["tone", "noise", "pn", "qpsk"],
            "snr_mode": ["auto", "fs", "ebno"],
        }

    def test_no_enums_is_empty(self):
        assert enums({"project": {"name": "p"}}) == {}


class TestResolve:
    def test_resolves_reference_to_string_enum(self):
        cfg = _cfg()
        assert (
            resolve_enum_type(cfg, "enum:wfm_type")
            == "string_enum:tone,noise,pn,qpsk"
        )

    def test_non_reference_passes_through(self):
        cfg = _cfg()
        assert resolve_enum_type(cfg, "double") == "double"
        assert resolve_enum_type(cfg, "string_enum:x,y") == "string_enum:x,y"

    def test_undefined_enum_raises(self):
        with pytest.raises(ValueError, match="undefined .*enum.* 'nope'"):
            resolve_enum_type(_cfg(), "enum:nope")


class TestInitParamsResolution:
    def test_codegen_sees_string_enum(self):
        # init_params() is the choke point every codegen consumer reads.
        types = {p[0]: p[1] for p in init_params(_cfg(), "syn")}
        assert types["kind"] == "string_enum:tone,noise,pn,qpsk"
        assert types["mode"] == "string_enum:auto,fs,ebno"
        assert types["fs"] == "double"
        # The resolved form is exactly what existing helpers expect.
        assert is_string_enum_type(types["kind"])
        assert string_enum_choices(types["kind"]) == [
            "tone",
            "noise",
            "pn",
            "qpsk",
        ]


class TestConsumerFacesReadSSOT:
    """The point of the SSOT: every codegen face reads the one declaration
    transparently, because resolution happens at the init_params() choke
    point. Here the `jm app` choice-flag derivation is the witness."""

    def test_app_choice_flag_from_enum_ref(self):
        from just_makeit import _app

        cfg = _cfg()
        flags = {f["name"]: f for f in _app._ctor_flags(cfg, "syn")}
        # the enum:wfm_type param becomes a choice flag carrying the resolved
        # value set — no choices were spelled out on the app side.
        assert flags["kind"].get("choices") == [
            "tone",
            "noise",
            "pn",
            "qpsk",
        ]
        # a plain scalar param stays a non-choice flag.
        assert flags["fs"].get("choices") is None


class TestRoundTrip:
    def test_save_load_preserves_reference_and_manifest_owns_enum(
        self, tmp_path
    ):
        save(tmp_path, _cfg())
        manifest = (tmp_path / "just-makeit.toml").read_text()
        # [[enum]] lives in the manifest, not an objects/ fragment.
        assert "[[enum]]" in manifest
        assert not (tmp_path / "objects").exists()
        # The reference is preserved verbatim on disk (not expanded).
        assert 'type = "enum:wfm_type"' in manifest
        assert "string_enum" not in manifest

        cfg2 = load(tmp_path)
        assert enums(cfg2) == enums(_cfg())
        # …but codegen still resolves it after a reload.
        kinds = {p[0]: p[1] for p in init_params(cfg2, "syn")}
        assert kinds["kind"] == "string_enum:tone,noise,pn,qpsk"
