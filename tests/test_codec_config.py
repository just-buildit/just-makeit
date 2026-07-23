"""Slice 1 of the variant codec: the `[codec.X]` model + config round-trip.

Covers the declarative codec table before any codegen exists — the SSOT parse
helpers (`_codec`), the `.pyi` union rendering, validation, and the fact that a
`[codec.X]` section survives a `_config` save/load unchanged and is never
mistaken for a component.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _codec as K
from just_makeit import _config as C
from just_makeit._new import run as new_run

_BLUE = {
    "discriminant": "char",
    "scalar_collapse": True,
    "entries": [
        {"code": "A", "ctype": "char", "bytes": True},
        {"code": "B", "ctype": "int8_t"},
        {"code": "I", "ctype": "int16_t"},
        {"code": "L", "ctype": "int32_t"},
        {"code": "X", "ctype": "int64_t"},
        {"code": "F", "ctype": "float"},
        {"code": "D", "ctype": "double"},
    ],
}


def _project(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", d, ["osc"], [("gain", "double", "1.0")])
    return d


# ── model helpers ─────────────────────────────────────────────────────────────


class TestModel:
    def test_entry_py_derived_from_ctype(self):
        assert (
            K.entry_py({"code": "A", "ctype": "char", "bytes": True}) == "str"
        )
        assert K.entry_py({"code": "X", "ctype": "int64_t"}) == "int"
        assert K.entry_py({"code": "D", "ctype": "double"}) == "float"

    def test_py_union_read_vs_write(self):
        # read (dict value): list[]; write (input arg): Sequence[]
        assert (
            K.codec_py_union(_BLUE, seq="list")
            == "str | int | float | list[int] | list[float]"
        )
        assert (
            K.codec_py_union(_BLUE, seq="Sequence")
            == "str | int | float | Sequence[int] | Sequence[float]"
        )

    def test_union_dedups_and_orders_int_before_float(self):
        # many int-widths collapse to one `int`; int precedes float.
        assert K.codec_py_union(_BLUE, seq="list").split(" | ")[:3] == [
            "str",
            "int",
            "float",
        ]

    def test_predicates(self):
        assert K.is_codec_method({"codec": "blue_keyword"})
        assert not K.is_codec_method({"name": "plain"})
        assert K.is_codec_property({"codec": "blue_keyword"})
        assert not K.is_codec_property({"name": "x"})


# ── validation ────────────────────────────────────────────────────────────────


class TestValidate:
    def test_accepts_a_well_formed_codec(self):
        K.validate_codec("blue_keyword", _BLUE)  # no raise

    def test_rejects_empty(self):
        with pytest.raises(K.CodecError, match="at least one entry"):
            K.validate_codec("x", {"entries": []})

    def test_rejects_unknown_ctype(self):
        with pytest.raises(K.CodecError, match="unknown ctype"):
            K.validate_codec(
                "x", {"entries": [{"code": "B", "ctype": "nope"}]}
            )

    def test_rejects_duplicate_code(self):
        bad = {
            "entries": [
                {"code": "B", "ctype": "int8_t"},
                {"code": "B", "ctype": "float"},
            ]
        }
        with pytest.raises(K.CodecError, match="duplicate code"):
            K.validate_codec("x", bad)

    def test_rejects_non_int_float_scalar(self):
        # a complex element is not a codec-legal numeric width.
        bad = {"entries": [{"code": "C", "ctype": "float _Complex"}]}
        with pytest.raises(K.CodecError, match="not an int or float"):
            K.validate_codec("x", bad)

    def test_bytes_entry_ctype_is_not_type_checked(self):
        # `char` is not a _CTYPE_META scalar, but a bytes branch is exempt.
        K.validate_codec(
            "x", {"entries": [{"code": "A", "ctype": "char", "bytes": True}]}
        )

    def test_rejects_bad_discriminant(self):
        bad = {
            "discriminant": "nope",
            "entries": [{"code": "B", "ctype": "int8_t"}],
        }
        with pytest.raises(K.CodecError, match="discriminant"):
            K.validate_codec("x", bad)


# ── config round-trip ─────────────────────────────────────────────────────────


class TestConfigRoundTrip:
    def test_codec_survives_save_load(self, tmp_path):
        d = _project(tmp_path)
        cfg = C.load(d)
        cfg["codec"] = {"blue_keyword": _BLUE}
        C.save(d, cfg)

        text = (d / "just-makeit.toml").read_text()
        assert "[codec.blue_keyword]" in text

        reloaded = C.load(d)
        assert reloaded["codec"] == {"blue_keyword": _BLUE}

    def test_codec_is_manifest_owned_not_a_component(self, tmp_path):
        d = _project(tmp_path)
        cfg = C.load(d)
        cfg["codec"] = {"blue_keyword": _BLUE}
        C.save(d, cfg)
        reloaded = C.load(d)
        assert "codec" not in C.components(reloaded)
        # osc is still the only component
        assert C.components(reloaded) == ["osc"]

    def test_codec_survives_an_unrelated_mutation(self, tmp_path):
        # a codec block declared, then another save (e.g. a later command) must
        # keep it byte-stable — the SSOT can't be dropped by a mutation.
        d = _project(tmp_path)
        cfg = C.load(d)
        cfg["codec"] = {"blue_keyword": _BLUE}
        C.save(d, cfg)
        cfg2 = C.load(d)
        C.save(d, cfg2)  # no-op resave
        assert C.load(d)["codec"] == {"blue_keyword": _BLUE}
