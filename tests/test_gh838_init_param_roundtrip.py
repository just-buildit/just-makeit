"""gh-838: an init-param key that survives one direction and not the other.

`_config._dump` wrote an init-param in **two** syntaxes — a
``[[<comp>.init_params]]`` table and a view's inline ``init_params = [{…}]``
— and each maintained its own idea of which keys existed. They drifted in
opposite directions. The table forgot `capsule` and `header`, so gh-790's
capsule init-param came back as a scalar of a C type jm does not know and the
next render died on ``_CTYPE_META['dp_sample_clock_t *']``; the inline form
forgot those *plus* `default_raw`, `real_type`, `real_create_fn` and `doc`.

`type` survived both times, which is what made it a crash rather than a
degradation: the param still looked declared, just not like a capsule.

The interesting part of this file is not the capsule case — it is
:func:`_maximal_param`. #826 was the same defect one key over, in the same
enumerated block, and its fix did not sweep the siblings. A test that names
the keys it checks would need updating by the same person who forgot the key,
so it derives them instead: it grows the tuple until
`init_param_tuple_to_dict` stops producing new ones, and asserts that
everything it can produce round-trips. A thirteenth field is covered without
this file being told it exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys as K  # noqa: E402
from just_makeit._script import _init_param_spec  # noqa: E402

#: Far past the twelve fields that exist; a bound, not an expectation.
_MAX_SLOTS = 40


def _maximal_param() -> dict:
    """The richest init-param dict `init_param_tuple_to_dict` can build.

    Grown a slot at a time until an extra one stops adding a key, so the key
    set comes from the code under test rather than from a list here that
    someone has to remember to extend.

    Every slot is filled with a distinct non-empty string. The two boolean
    slots (`optional`, `required`) become `True`, which is what the writer
    keys on — a falsy value would make them absent and quietly untested.
    """
    previous: dict = {}
    for n in range(3, _MAX_SLOTS):
        current = C.init_param_tuple_to_dict(_slots(n))
        if n > 3 and set(current) == set(previous):
            return previous
        previous = current
    raise AssertionError(
        f"init_param_tuple_to_dict still growing at {_MAX_SLOTS} slots"
    )


def _slots(n: int) -> tuple:
    """*n* filled slots, minus the ones no single param may hold at once.

    gh-1224 made the key set a union over MUTUALLY EXCLUSIVE shapes rather
    than one flat list: `object` resolves to `capsule`/`header`, so a param
    carrying both is refused and the writer strips the resolution. A probe
    that filled every slot regardless would therefore describe a param that
    cannot exist, and -- worse -- would have reported `capsule` and `header`
    as unwritable, which is the exact false negative gh-838 exists to catch.
    So the object slot is left empty here and covered by
    :func:`_maximal_object_param`.
    """
    return tuple("" if i == 15 else f"v{i}" for i in range(n))


def _maximal_object_param() -> dict:
    """The richest init-param dict of the gh-1224 `object` shape.

    Its resolved slots (`capsule`, `header`) are derived state and are
    deliberately NOT written back, so this shape's key set is the other half
    of the union the writer can produce.
    """
    return C.init_param_tuple_to_dict(
        tuple(f"v{i}" for i in range(_OBJECT_SLOTS))
    )


#: Wide enough to reach slot 15 (`object`); the two slots after it are the
#: resolution, which `init_param_tuple_to_dict` never persists.
_OBJECT_SLOTS = 16


def _roundtrip(tmp_path: Path, cfg: dict) -> dict:
    """`C.save` then `C.load`, which is the pair that lost the keys."""
    C.save(tmp_path, cfg)
    return C.load(tmp_path)


class TestTheKeySetIsDerived:
    """The guard that makes the rest of this file self-maintaining."""

    def test_the_writer_knows_every_key_the_reader_can_produce(self):
        # `_INIT_PARAM_KEYS` is the one list both syntaxes render from. If a
        # key can be built but not written, it is lost on the next save —
        # which is the whole defect, stated as an invariant.
        written = {key for key, _is_bool in K.INIT_PARAM_FIELDS}
        # gh-1224: the union over both shapes, because no ONE param may carry
        # `object` and `capsule` at once. Checking only one shape would let a
        # key be buildable-but-unwritable in the other and lose it on save --
        # gh-838's defect, one shape over.
        produced = set(_maximal_param()) | set(_maximal_object_param())
        assert produced <= written
        # And the validator's set is derived from the same list, so a key
        # cannot be legal-to-author while unwritable — which is the state
        # gh-838 was actually in: `INIT_PARAM_KEYS` already listed `capsule`
        # and `header` while `_dump` had no branch emitting them.
        assert K.INIT_PARAM_KEYS == written

    def test_the_probe_actually_finds_the_known_keys(self):
        # A `_maximal_param` that silently returned {} would make every
        # assertion below vacuous.
        found = set(_maximal_param())
        assert {"name", "type", "capsule", "header", "doc"} <= found
        assert len(found) >= 12
        # ...and the object shape's probe is not vacuous either, or the union
        # above would silently collapse back to one shape.
        assert "object" in set(_maximal_object_param())


class TestTheObjectForm:
    """``[[<comp>.init_params]]`` — the syntax that produced the crash."""

    def test_every_key_survives(self, tmp_path):
        param = _maximal_param()
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "init_params": [param],
            },
        }
        back = _roundtrip(tmp_path, cfg)["capture"]["init_params"][0]
        assert back == param

    def test_a_capsule_param_comes_back_as_a_capsule(self, tmp_path):
        # The reported case, stated in its own terms rather than only as an
        # instance of the sweep above.
        param = C.init_param_tuple_to_dict(
            (
                "clock",
                "dp_sample_clock_t *",
                "",
                "",
                "",
                "",
                False,
                "",
                True,
                "",
                "doppler.clk",
                "clk.h",
            )
        )
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "init_params": [param],
            },
        }
        back = _roundtrip(tmp_path, cfg)["capture"]["init_params"][0]
        assert back["capsule"] == "doppler.clk"
        assert back["header"] == "clk.h"
        # And the projection the renderer consumes: `capsule` at 10, `header`
        # at 11. Reading it back as a bare scalar is what raised KeyError.
        projected = C.init_params(_roundtrip(tmp_path, cfg), "capture")[0]
        assert projected[10] == "doppler.clk"
        assert projected[11] == "clk.h"


class TestTheViewInlineForm:
    """``init_params = [{…}]`` under ``[[<comp>.views]]`` (gh-504)."""

    def _cfg(self, param: dict) -> dict:
        return {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "views": [
                    {
                        "class_name": "Slice",
                        "create_fn": "capture_slice",
                        "init_params": [param],
                    }
                ],
            },
        }

    def test_every_key_survives(self, tmp_path):
        param = _maximal_param()
        back = _roundtrip(tmp_path, self._cfg(param))
        assert back["capture"]["views"][0]["init_params"][0] == param

    def test_a_multiline_doc_survives_as_one_line(self, tmp_path):
        # An inline table cannot hold TOML's `\"\"\"` form, and the previous
        # emitter's answer was to drop `doc` entirely. Escaping it keeps the
        # author's prose; the newline is what makes this worth asserting.
        param = {
            "name": "gain",
            "type": "double",
            "doc": "first line\nsecond line",
        }
        back = _roundtrip(tmp_path, self._cfg(param))
        assert (
            back["capture"]["views"][0]["init_params"][0]["doc"]
            == "first line\nsecond line"
        )

    def test_a_quote_in_the_doc_does_not_break_the_table(self, tmp_path):
        param = {
            "name": "gain",
            "type": "double",
            "doc": 'he said "hi" \\ ok',
        }
        back = _roundtrip(tmp_path, self._cfg(param))
        assert (
            back["capture"]["views"][0]["init_params"][0]["doc"]
            == 'he said "hi" \\ ok'
        )


class TestEscapingSurvivesItsOwnOutput:
    """A manifest `C.save` writes and `C.load` refuses is worse than a drop.

    `_dump` self-checks with `tomllib.loads` and, on failure, returns the text
    anyway — so a bad escape is not caught at write time. Both of these were
    found by review, and the first is a regression this very fix introduced:
    the inline emitter only started writing `doc` here.
    """

    def _view_cfg(self, doc: str) -> dict:
        return {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "views": [
                    {
                        "class_name": "Slice",
                        "create_fn": "capture_slice",
                        "init_params": [
                            {"name": "g", "type": "double", "doc": doc}
                        ],
                    }
                ],
            },
        }

    def test_a_carriage_return_in_an_inline_doc(self, tmp_path):
        # TOML forbids a raw CR in a basic string. Prose lifted out of a
        # CRLF-authored header is enough to hit it.
        doc = "first line\r\nsecond line"
        back = _roundtrip(tmp_path, self._view_cfg(doc))
        assert back["capture"]["views"][0]["init_params"][0]["doc"] == doc

    def test_a_tab_in_an_inline_doc(self, tmp_path):
        doc = "aligned\tcolumn"
        back = _roundtrip(tmp_path, self._view_cfg(doc))
        assert back["capture"]["views"][0]["init_params"][0]["doc"] == doc

    def test_a_quote_in_a_block_scalar(self, tmp_path):
        # Pre-existing, and reachable from any `default_raw` holding a C
        # expression with a string literal: the block form wrote every
        # non-doc value with a bare f-string, so this emitted
        # `default_raw = "a"b"`.
        param = {"name": "g", "type": "const char *", "default_raw": 'a"b'}
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "init_params": [param],
            },
        }
        back = _roundtrip(tmp_path, cfg)["capture"]["init_params"][0]
        assert back["default_raw"] == 'a"b'

    def test_a_backslash_in_a_block_scalar(self, tmp_path):
        param = {"name": "g", "type": "const char *", "default": "C:\\tmp"}
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "init_params": [param],
            },
        }
        back = _roundtrip(tmp_path, cfg)["capture"]["init_params"][0]
        assert back["default"] == "C:\\tmp"


class TestTheScriptReplay:
    """`jm script` must re-declare what the manifest now keeps."""

    def test_one_emitter_serves_the_object_and_the_view(self):
        # gh-838: the object path had grown the gh-790 capsule grammar and the
        # view path had not. That was invisible only because the view's
        # `capsule`/`header` never survived a save — making them survive is
        # what would have turned it into a script that rebuilds a capsule
        # param as a scalar of a type jm does not know.
        param = {
            "name": "clk",
            "type": "dp_clk_t *",
            "required": True,
            "capsule": "doppler.clk",
            "header": "clk.h",
        }
        assert (
            _init_param_spec(param)
            == "clk:dp_clk_t *:capsule:doppler.clk:clk.h"
        )

    def test_the_other_grammars_are_unchanged(self):
        assert _init_param_spec({"name": "x", "type": "int"}) == "x:int"
        assert (
            _init_param_spec({"name": "g", "type": "double", "default": "1.0"})
            == "g:double:1.0"
        )
        assert (
            _init_param_spec(
                {"name": "fs", "type": "double", "required": True}
            )
            == "fs:double:required"
        )
        assert (
            _init_param_spec(
                {
                    "name": "a",
                    "type": "float[]",
                    "optional": True,
                    "create_fn": "mk",
                }
            )
            == "a:float[]:optional:mk"
        )

    def test_a_capsule_param_outranks_its_required_flag(self):
        # A capsule param carries `required = true` as well, and slot 3 holds
        # one positional word. Emitting `:required` would lose the capsule.
        spec = _init_param_spec(
            {
                "name": "clk",
                "type": "dp_clk_t *",
                "required": True,
                "capsule": "doppler.clk",
            }
        )
        assert spec == "clk:dp_clk_t *:capsule:doppler.clk"


class TestTheModuleFormIsADifferentGrammar:
    """`[[module.X.init_params]]` looked like a third instance. It is not."""

    def test_its_reader_consumes_only_three_keys(self, tmp_path):
        # `module_init_params` projects `(name, type, default)` and nothing
        # else, so the dumper writing exactly those three is symmetric, not a
        # truncation. Asserted rather than assumed, because the obvious
        # "consistency" fix here — copy the object form's key list over — would
        # write keys that nothing reads and imply a capsule module can take a
        # capsule create-arg, which it cannot.
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "module": {
                "m": {
                    "kind": "capsule",
                    "backing": "thing",
                    "objects": [],
                    "init_params": [
                        {"name": "rate", "type": "double", "default": "1.0"}
                    ],
                }
            },
        }
        back = _roundtrip(tmp_path, cfg)
        assert C.module_init_params(back, "m") == [("rate", "double", "1.0")]


class TestTheTwoSyntaxesAgree:
    """The drift itself, asserted directly."""

    def test_they_write_the_same_key_set(self):
        param = _maximal_param()
        block = {
            line.split(" = ", 1)[0]
            for line in C._init_param_block_lines(param)
        }
        inline = {
            piece.split(" = ", 1)[0]
            for piece in C._init_param_inline(param).strip("{}").split(", ")
        }
        assert block == inline == set(param)
