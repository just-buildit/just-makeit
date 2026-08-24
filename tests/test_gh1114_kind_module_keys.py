"""gh-1114: a `kind`-bearing module's keys are checked against a vocabulary.

`unknown_keys` used to skip a handle / capsule / composer module outright, so
**nothing** was checked there: a key from the wrong face and an outright typo
both reported clean and both did nothing. That silence is what made gh-1111
hard to see from outside — three keys written on one method, one honoured, no
warning, and the manifest read as if it said something it did not.

Two properties, and the second is the one that decides whether this feature
survives contact with a real project:

- a wrong key is **named**, and where jm can, the right spelling for this face
    is named with it;
- a **correct** module is silent. A vocabulary that warns on real manifests is
    worse than no check at all — it teaches people to ignore the warning.

The second is why `TestTheVocabularyIsComplete` exists. The vocabularies were
assembled by RUNNING, not by reading: code archaeology alone cannot produce
them (`error_message` is valid on a handle method and is read in
`_context/_diagnostics`, so it escapes any grep of the three generator files,
and the docs' key tables are partial). What makes them trustworthy is that
every manifest jm itself writes round-trips through them clean.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _keys


def _msgs(cfg):
    return [u.message() for u in _keys.unknown_keys(cfg)]


def _handle_module(**over):
    m = {
        "kind": "handle",
        "backing": "b",
        "header": "b/b.h",
        "type_name": "Dev",
        "close_fn": "b_close",
        "create_fn": "b_open",
        "context_manager": True,
        "package": "pkg",
        "depends_on": [{"name": "b", "link": True}],
        "extra_link_libs": ["m"],
        "create_args": [
            {"name": "path", "type": "path"},
            {
                "name": "mode",
                "type": "int",
                "enum": "m",
                "default": "raw",
                "kwonly": True,
            },
        ],
        "create_post": [{"fn": "b_set", "when": "mode", "arg": "mode"}],
        "methods": [
            {
                "name": "drain",
                "fn": "b_drain",
                "returns": "int",
                "error": "OSError",
                "error_message": "budget ran out",
                "nogil": True,
                "args": [{"name": "n", "type": "size_t", "default": "0"}],
            },
        ],
        "getters": [
            {
                "fn": "b_stats",
                "out": "b_stats_t",
                "cache": False,
                "fields": [
                    {
                        "name": "used",
                        "from": "n",
                        "type": "size_t",
                        "scale": "1e-9",
                        "writable_fn": "b_set_used",
                    }
                ],
            },
        ],
        "factories": [
            {
                "name": "FromFile",
                "create_fn": "b_load",
                "init_params": [{"name": "path", "type": "path"}],
            },
        ],
    }
    m.update(over)
    return {"project": {"name": "p"}, "module": {"dev": m}}


class TestAWrongKeyIsNamed:
    def test_the_gh1111_case(self):
        """`status_return` beside a working `error`: inert, and silent."""
        cfg = _handle_module()
        cfg["module"]["dev"]["methods"][0]["status_return"] = True
        msgs = _msgs(cfg)
        assert len(msgs) == 1, msgs
        assert "status_return" in msgs[0]
        # ...and it names the spelling that works here.
        assert 'error = "<Exception>"' in msgs[0]

    def test_an_outright_typo(self):
        cfg = _handle_module()
        cfg["module"]["dev"]["methods"][0]["nogilXX"] = True
        msgs = _msgs(cfg)
        assert len(msgs) == 1 and "nogilXX" in msgs[0]

    def test_the_object_signature_spelling_on_a_handle_method(self):
        """`arg_type`/`return_type` are how a CAPSULE method declares its
        signature, so this is the confusion most likely to look right."""
        cfg = _handle_module()
        cfg["module"]["dev"]["methods"][0]["arg_type"] = "double"
        msgs = _msgs(cfg)
        assert len(msgs) == 1
        assert "args = [{ name = ..., type = ... }]" in msgs[0]

    def test_a_nested_arg_row_is_checked(self):
        cfg = _handle_module()
        cfg["module"]["dev"]["methods"][0]["args"][0]["typo"] = 1
        assert any("typo" in m for m in _msgs(cfg))

    def test_a_getter_field_row_is_checked(self):
        cfg = _handle_module()
        cfg["module"]["dev"]["getters"][0]["fields"][0]["scaleX"] = 1
        assert any("scaleX" in m for m in _msgs(cfg))

    def test_a_sub_table_jm_has_no_vocabulary_for_is_reported(self):
        """An unwalked table is exactly the state this issue is about, so it
        must not be reachable by adding one.

        `enums`, not an invented name: a made-up table is caught one step
        earlier as an unknown MODULE key, so it never reaches this branch and
        the first version of this test passed with the branch deleted. It has
        to be a table whose key is valid and whose rows have no vocabulary.
        """
        cfg = _handle_module(enums=[{"name": "ftype"}])
        msgs = _msgs(cfg)
        assert len(msgs) == 1, msgs
        assert "no key vocabulary" in msgs[0] and "enums" in msgs[0]


class TestACorrectModuleIsSilent:
    def test_a_fully_populated_handle_module(self):
        assert _msgs(_handle_module()) == []

    @pytest.mark.parametrize("kind", ["capsule", "composer"])
    def test_the_other_two_faces(self, kind):
        cfg = {
            "project": {"name": "p"},
            "module": {
                "m": {
                    "kind": kind,
                    "backing": "b",
                    "header": "b/b.h",
                    "depends_on": [{"name": "b", "link": True}],
                    # These two spell a signature the way a handle method
                    # may NOT, which is why the vocabularies are per-kind.
                    "methods": [
                        {
                            "name": "step",
                            "arg_type": "double",
                            "return_type": "double",
                            "nogil": True,
                        }
                    ],
                }
            },
        }
        assert _msgs(cfg) == []

    def test_a_kind_jm_does_not_generate_is_left_alone(self):
        """jm should not lecture a project about a face it does not own."""
        cfg = {"module": {"m": {"kind": "something_else", "anything": 1}}}
        assert _msgs(cfg) == []


class TestTheVocabularyIsComplete:
    """The property that keeps this from becoming noise.

    Not a list of expected keys — a round-trip. Whatever jm's own writer
    emits must be a key jm's own checker recognises, so a new key added to
    one and not the other fails here rather than in a user's terminal.
    """

    def test_what_jm_dumps_it_recognises(self):
        cfg = _handle_module()
        reloaded = tomllib.loads(C._dump(cfg))
        assert _msgs(reloaded) == [], C._dump(cfg)

    def test_every_bundled_example_manifest_is_clean(self):
        """The examples are real projects jm builds end-to-end."""
        root = Path(__file__).parent.parent / "src/just_makeit/examples"
        seen = 0
        for f in sorted(root.rglob("*.toml")):
            try:
                cfg = tomllib.loads(f.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                continue
            if not any(
                isinstance(d, dict) and d.get("kind")
                for d in (cfg.get("module") or {}).values()
            ):
                continue
            seen += 1
            assert _msgs(cfg) == [], f"{f}: {_msgs(cfg)}"
        # Prove the scan was armed rather than vacuously empty.
        assert seen > 0, "no bundled example declares a kind-bearing module"


def _every_handle_key_module():
    """A handle module exercising EVERY key in its vocabulary.

    Hand-written on purpose. Deriving it from the vocabulary would make the
    test tautological — delete a key and it leaves the fixture too, and the
    module stays silent. Hand-written, deleting a key from the vocabulary
    turns this module's use of it into a finding.

    Written after the first version of this file failed its own sabotage
    check: removing `out_len_fn` from `HANDLE_METHOD_KEYS` changed nothing,
    because no fixture used it. A vocabulary is only gated for the keys some
    fixture actually writes.
    """
    return {
        "project": {"name": "p"},
        "module": {
            "dev": {
                "kind": "handle",
                "backing": "b",
                "package": "pkg",
                "header": "b/b.h",
                "handle_type": "b_t",
                "type_name": "Dev",
                "create_fn": "b_open",
                "init_fn": "b_init",
                "close_fn": "b_close",
                "close_returns": "int",
                "context_manager": True,
                "optional_backend": True,
                "create_error": "OSError",
                "create_error_message": "no",
                "capsule": "p.b",
                "capsule_name": "cap",
                "doc": "d",
                "no_generate": False,
                "serializable": True,
                "reexports": [],
                "extra_types": [],
                "enums": [],
                "functions_in_core": True,
                "extra_link_libs": ["m"],
                "extra_include_dirs": ["x"],
                "depends_on": [
                    {"name": "b", "link": True, "test_only": False}
                ],
                "init_params": [
                    {
                        "name": "p",
                        "type": "path",
                        "default": "x",
                        "enum": "e",
                        "capsule": "c",
                        "header": "h",
                        "doc": "d",
                        "kwonly": True,
                    }
                ],
                "create_args": [
                    {
                        "name": "path",
                        "type": "path",
                        "enum": "e",
                        "default": "d",
                        "kwonly": True,
                        "capsule": "c",
                        "header": "h",
                        "doc": "d",
                    }
                ],
                "create_post": [{"fn": "f", "when": "w", "arg": "a"}],
                "properties": [
                    {
                        "name": "g",
                        "type": "double",
                        "writable": True,
                        "doc": "d",
                        "enum": "e",
                        "getter": "gg",
                        "setter": "ss",
                        "fn": "f",
                    }
                ],
                "functions": [{"name": "f", "params": []}],
                "methods": [
                    {
                        "name": "drain",
                        "fn": "b_drain",
                        "returns": "int",
                        "nogil": True,
                        "error": "OSError",
                        "error_message": "m",
                        "out_len_fn": "b_len",
                        "doc": "d",
                        "args": [
                            {
                                "name": "n",
                                "type": "size_t",
                                "default": "0",
                                "writable": True,
                                "enum": "e",
                                "capsule": "c",
                                "kwonly": True,
                            }
                        ],
                    }
                ],
                "getters": [
                    {
                        "fn": "b_stats",
                        "out": "b_t",
                        "cache": True,
                        "doc": "d",
                        "fields": [
                            {
                                "name": "u",
                                "from": "n",
                                "type": "size_t",
                                "enum": "e",
                                "scale": "1",
                                "expr": "x",
                                "getter": "g",
                                "writable_fn": "w",
                                "writable": True,
                                "doc": "d",
                            }
                        ],
                    }
                ],
                "factories": [
                    {
                        "name": "F",
                        "create_fn": "b_load",
                        "doc": "d",
                        "init_params": [{"name": "p", "type": "path"}],
                    }
                ],
            }
        },
    }


def test_every_vocabulary_key_is_exercised_by_a_fixture():
    """Delete any handle key from the vocabulary and this goes red.

    This is the gate the first version of this file lacked: without it a
    vocabulary entry could be removed and nothing would notice until a real
    project started warning about a key that works.
    """
    assert _msgs(_every_handle_key_module()) == []
