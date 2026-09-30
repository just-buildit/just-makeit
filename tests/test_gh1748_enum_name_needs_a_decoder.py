"""gh-1748: a constant-bound enum's reverse lookup is emitted only where decoded.

An ``[[enum]]`` that declares ``enumerators`` (gh-1450) gets, beside its
tables, a ``static inline const char *_enum_<name>_name(long v)`` that maps
a C value back to its choice string. Only `_enumc.decode_c` and
`_enumc.name_expr` call it. A face that only LOOKS the enum up -- a module
function parameter, a method parameter, a handle create-arg, a composer
serializer parameter, the composer's C CLI -- never decodes, and clang
reports the uncalled function as ``-Wunused-function`` in a main file.

Each face now passes ``decoded`` from the predicate its decode call sites
are emitted from, the mirror of gh-1745's ``looked_up``. The module-function
and object faces are built under ``-Werror`` by gcc and clang in
``tests/test_preset_build.py`` (``module_enum_constants_*``); the handle
and composer faces need an author's C backing to build, so they are asserted
here, on the render, in both directions -- dropping the function where a
getter calls it is a compile error, not a warning.
"""

from __future__ import annotations

from just_makeit import _config as C
from just_makeit import _enumc

ENUM = {
    "name": "lvl",
    "values": ["auto", "debug"],
    "enumerators": ["LVL_AUTO", "LVL_DEBUG"],
}
NAME_FN = "_enum_lvl_name(long v)"


def _handle_cfg(*, create_args=(), getter_fields=()) -> dict:
    return {
        "enum": [ENUM],
        "module": {
            "rdr": {
                "kind": "handle",
                "backing": "rdr",
                "type_name": "Rdr",
                "create_fn": "rdr_open",
                "create_args": list(create_args),
                "getters": [
                    {
                        "fn": "rdr_info",
                        "out": "rdr_info_t",
                        "fields": list(getter_fields),
                    }
                ],
            }
        },
    }


_ARG = {"name": "level", "type": "const char *", "enum": "lvl"}
_GETTER = {"name": "level", "type": "int", "enum": "lvl"}


def test_a_create_arg_only_handle_gets_no_reverse_lookup() -> None:
    from just_makeit._handle import render_enum_tables

    tables = render_enum_tables(_handle_cfg(create_args=[_ARG]), "rdr")
    assert "static const int _enum_lvl_c[]" in tables
    assert NAME_FN not in tables


def test_a_handle_getter_still_gets_the_reverse_lookup_it_calls() -> None:
    from just_makeit._handle import render_enum_tables, render_getsets

    cfg = _handle_cfg(create_args=[_ARG], getter_fields=[_GETTER])
    assert NAME_FN in render_enum_tables(cfg, "rdr")
    assert "_enum_lvl_name(_v)" in render_getsets(cfg, "rdr")[0]


def test_an_expr_getter_decodes_nothing() -> None:
    """An ``expr`` field returns its expression; the enum is not decoded."""
    from just_makeit._handle import render_enum_tables, render_getsets

    expr = {**_GETTER, "expr": "tmp.level + 0"}
    cfg = _handle_cfg(getter_fields=[expr])
    assert "_enum_lvl_name(" not in render_getsets(cfg, "rdr")[0]
    assert NAME_FN not in render_enum_tables(cfg, "rdr")


def _composer_cfg(*, source_fields=(), serializer_params=()) -> dict:
    return {
        "enum": [ENUM],
        "module": {
            "c": {
                "kind": "composer",
                "source": {"fields": list(source_fields)},
                "segment": {"fields": []},
                "serializers": [
                    {"name": "ser", "fn": "c_ser"}
                    | {"params": list(serializer_params)}
                ],
            }
        },
    }


def test_a_serializer_param_only_composer_gets_no_reverse_lookup() -> None:
    from just_makeit._composer import render_enum_tables

    cfg = _composer_cfg(serializer_params=[{"name": "k", "enum": "lvl"}])
    assert NAME_FN not in render_enum_tables(cfg, "c")


def test_a_composer_source_field_gets_it_and_the_cli_does_not() -> None:
    """The extension decodes a source field; the C CLI only checks flags."""
    from just_makeit._composer import render_cli, render_enum_tables

    cfg = _composer_cfg(
        source_fields=[{"name": "level", "type": "int", "enum": "lvl"}]
    )
    cfg["project"] = {"name": "p"}
    cfg["module"]["c"]["backing"] = "c"
    cfg["module"]["c"]["source"]["struct"] = "c_src_t"
    cfg["module"]["c"]["segment"]["struct"] = "c_seg_t"
    assert NAME_FN in render_enum_tables(cfg, "c")
    cli = render_cli(cfg, "c")
    assert "static const int _enum_lvl_c[]" in cli
    assert "_enum_lvl_name(" not in cli


def test_an_object_method_param_gets_no_reverse_lookup() -> None:
    from just_makeit._context._methods import make_enum_tables_ctx

    methods = [{"name": "pick", "params": [{"name": "k", "enum": "lvl"}]}]
    out = make_enum_tables_ctx(
        "o", "O", methods=methods, enums=C.enums({"enum": [ENUM]})
    )["enum_tables"]
    assert "static const int _enum_O_lvl_c[]" in out
    assert "_enum_O_lvl_name(long v)" not in out


def test_an_object_property_gets_the_reverse_lookup() -> None:
    from just_makeit._context._methods import make_enum_tables_ctx

    props = [{"name": "level", "type": "int", "enum": "lvl"}]
    out = make_enum_tables_ctx(
        "o", "O", properties=props, enums=C.enums({"enum": [ENUM]})
    )["enum_tables"]
    assert "_enum_O_lvl_name(long v)" in out


def test_the_default_decodes_every_enum() -> None:
    """``decoded=None`` keeps the reverse lookup for every constant enum."""
    out = _enumc.render_tables(["lvl"], C.enums({"enum": [ENUM]}))
    assert NAME_FN in out
