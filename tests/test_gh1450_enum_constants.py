"""gh-1450: an ``[[enum]]`` that names its C constants binds to them.

The compiled proof is the ``enum_constants`` example: a C enum at -1, 10,
20, 30 crossed from Python through a constructor, a property and a method.
This covers what an example cannot reach cheaply: that a malformed
declaration is refused before anything renders, and that every OTHER face
-- a module function, a handle, a composer, a C app -- carries constants
through the one registry, and none binds a position.
"""

from __future__ import annotations

import pytest

from just_makeit import _config as C
from just_makeit import _enumc

CFG = {
    "enum": [
        {
            "name": "lvl",
            "values": ["auto", "debug", "warn"],
            "enumerators": ["LVL_AUTO", "LVL_DEBUG", "LVL_WARN"],
        }
    ]
}


def _errors(**enum) -> list:
    base = {"name": "lvl", "values": ["a", "b"]}
    return C.manifest_type_errors({"enum": [{**base, **enum}]})


def test_the_registry_carries_the_constants() -> None:
    reg = C.enums(CFG)
    assert list(reg["lvl"]) == ["auto", "debug", "warn"]
    assert reg["lvl"].constants == ["LVL_AUTO", "LVL_DEBUG", "LVL_WARN"]
    assert (
        C.enums({"enum": [{"name": "x", "values": ["a"]}]})["x"].constants
        is None
    )


@pytest.mark.parametrize(
    "enum, needle",
    [
        ({"enumerators": ["A"]}, "1 enumerators for 2 values"),
        ({"enumerators": ["A", "not-c"]}, "must be C identifiers"),
        (
            {"values": ["a=1", "b"], "enumerators": ["A", "B"]},
            "cannot contain",
        ),
    ],
)
def test_a_malformed_declaration_is_refused(enum, needle) -> None:
    errs = _errors(**enum)
    assert any(needle in e for e in errs), errs


def test_a_constructor_param_resolves_to_its_constants() -> None:
    spec = C.resolve_enum_type(CFG, "enum:lvl")
    assert spec == ("string_enum:auto=LVL_AUTO,debug=LVL_DEBUG,warn=LVL_WARN")


def test_the_lookup_maps_its_index_to_the_constant() -> None:
    reg = C.enums(CFG)
    code = _enumc.validate_c("level", "lvl", reg)
    assert "int _arg_level_i = _enum_index(_enum_lvl, level);" in code
    assert "if (_arg_level_i < 0)" in code
    assert "int _arg_level = _enum_lvl_c[_arg_level_i];" in code


def test_without_constants_nothing_changes() -> None:
    reg = C.enums({"enum": [{"name": "lvl", "values": ["a", "b"]}]})
    code = _enumc.validate_c("level", "lvl", reg)
    assert "_c[" not in code and "_arg_level_i" not in code
    tables = _enumc.render_tables(["lvl"], reg)
    assert "_enum_lvl_c" not in tables


def test_decode_searches_the_constants() -> None:
    reg = C.enums(CFG)
    tables = _enumc.render_tables(["lvl"], reg)
    assert "static const int _enum_lvl_c[] = {" in tables
    assert "LVL_AUTO," in tables and "_enum_lvl_name(long v)" in tables
    body = _enumc.decode_c("level", "lvl", "self->x", reg)
    assert "_enum_lvl_name(_v)" in body and "[_v]" not in body


def test_a_module_function_binds_constants() -> None:
    from just_makeit._render import make_functions_ctx

    fns = [
        {
            "name": "f",
            "return_type": "int",
            "params": [{"name": "level", "type": "int", "enum": "lvl"}],
        }
    ]
    w = make_functions_ctx(
        "m", "M", fns, C.enums(CFG), owner={"project": {"name": "p"}}
    )
    assert "_enum_lvl_c[" in w["function_wrappers"]
    assert "LVL_WARN" in w["function_enum_tables"]


def test_a_c_app_hands_create_the_constant() -> None:
    from just_makeit import _app

    flag = {
        "name": "level",
        "type": "int",
        "default": "auto",
        "help": "",
        "ctor": True,
        "choices": ["auto", "debug", "warn"],
        "constants": ["LVL_AUTO", "LVL_DEBUG", "LVL_WARN"],
    }
    assert _app._ctor_c_args([flag], parsed=True) == "jm_values_level[level]"
    parsers = _app._c_choice_parsers([flag])
    assert "jm_values_level[] = {LVL_AUTO, LVL_DEBUG, LVL_WARN};" in parsers


def test_a_composer_default_is_the_constant() -> None:
    reg = C.enums(CFG)
    assert _enumc.default_c("lvl", "debug", reg) == "LVL_DEBUG"
    assert _enumc.default_c("lvl", "nope", reg) == "LVL_AUTO"
    plain = C.enums({"enum": [{"name": "lvl", "values": ["a", "b"]}]})
    assert _enumc.default_c("lvl", "b", plain) == "1"
