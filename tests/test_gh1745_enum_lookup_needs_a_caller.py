"""gh-1745 item 1: the string-enum lookup is emitted only when something calls it.

`_enumc.render_tables` emitted the ``static int _enum_index[_<Type>]`` lookup
beside the choice tables on every face that referenced an ``[[enum]]``. Only a
`_enumc.validate_c` call site -- a setter, a parameter, a constructor argument
-- ever calls it; a face that only DECODES (a read-only property, a handle
getter) indexes the table and nothing else. doppler, building with
``-Wall -Wextra -Werror``, had three such extensions fail on
``-Wunused-function`` (``sample_clock``, ``wfm_plan``, ``wfm_reader``).

Each face now passes ``looked_up`` -- the enums its call sites look up -- from
the same predicate its call sites are emitted from, so the lookup and its
callers cannot disagree. The object face is proven by a build under
``-Werror`` in ``tests/test_preset_build.py`` (``module_enum_decode_only`` /
``module_enum_looked_up``); the handle face needs an author's C backing to
build, so it is asserted here, on the render, in both directions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _enumc  # noqa: E402
from just_makeit._handle import render_enum_tables, render_tp_init  # noqa: E402

ENUMS = [{"name": "ftype", "values": ["raw", "wav", "blue"]}]


def _cfg(*, create_args=(), getter_fields=()):
    return {
        "enum": ENUMS,
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


_GETTER = {"name": "file_type", "type": "int", "enum": "ftype"}
_ARG = {"name": "kind", "type": "const char *", "enum": "ftype"}


def test_a_getter_only_handle_gets_the_table_and_no_lookup():
    """doppler's ``wfm_plan``: the enum is decoded, never looked up."""
    cfg = _cfg(getter_fields=[_GETTER])
    tables = render_enum_tables(cfg, "rdr")
    assert "static const char *const _enum_ftype[]" in tables
    assert "_enum_index(" not in tables
    assert "_enum_index(" not in render_tp_init(cfg, "rdr")


def test_a_create_arg_still_gets_the_lookup_it_calls():
    """The other direction: dropping the lookup where a call site needs it
    is a compile error, not a warning."""
    cfg = _cfg(create_args=[_ARG], getter_fields=[_GETTER])
    assert "_enum_index(const char *const *tab" in render_enum_tables(
        cfg, "rdr"
    )
    assert "_enum_index(_enum_ftype, kind)" in render_tp_init(cfg, "rdr")


def test_the_default_keeps_every_other_face_unchanged():
    """``looked_up=None`` is every face whose each reference is a lookup --
    module functions and composers -- and must still emit it."""
    out = _enumc.render_tables(["ftype"], {"ftype": ["raw"]})
    assert "_enum_index(const char *const *tab" in out
