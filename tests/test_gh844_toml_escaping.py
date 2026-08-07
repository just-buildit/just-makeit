"""gh-844: four hand-rolled TOML escapers, three of which emitted bad TOML.

Measured on released 0.53.0, and reproduced exactly here before the fix:

===========================  =======  ======  =======
path                         lone CR  U+007F  newline
===========================  =======  ======  =======
``_toml_inline_string``      ok       reject  ok
``_str_assign``              reject   reject  ok
``_toml_scalar``             reject   reject  reject
``_toml_value``              reject   reject  reject
===========================  =======  ======  =======

"reject" = `tomllib.loads` refuses what the function just produced.

Two lessons are encoded in how this file is written.

**The matrix, not a remembered list.** gh-838 fixed the inline path with
``json.dumps`` on the reasoning that JSON's escape set is a subset of TOML's.
It is, except for ``U+007F`` — wrong by exactly one codepoint, which is what a
remembered list gets you. So every path is checked against every class of
character TOML forbids, and the classes come from the spec rather than from
what someone recalled breaking.

**A discovery ratchet.** `test_every_escaper_is_covered` finds the escapers by
name and fails if one is not in the matrix, so a fifth added later cannot be
silently untested — the same failure that let `_toml_scalar` keep a two-rule
escape while its siblings were fixed twice around it.

The other half of the issue is `_dump`'s self-check, which caught all of this
and *returned the bad text anyway*. That is why three separate escaping bugs
survived: the manifest broke the **next** command, in a different verb from
the one that wrote it.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    # The same shim `_config` uses. Importing `tomllib` unconditionally is
    # what broke this file on the 3.9 and 3.10 matrix legs while a local 3.12
    # run stayed green — it is stdlib only from 3.11, and a test ABOUT the
    # TOML parser is the last place to assume the parser is importable.
    import tomli as tomllib

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402

#: Every class TOML 1.0.0 forbids raw inside a basic string, plus the two
#: ordinary metacharacters. Named so a failure says which class broke.
FORBIDDEN = {
    "lone-CR": "a\rb",
    "DEL-U+007F": "a\x7fb",
    "NUL-adjacent-C0": "a\x01b",
    "unit-separator-U+001F": "a\x1fb",
    "newline": "a\nb",
    "CRLF": "a\r\nb",
    "tab": "a\tb",
    "quote": 'a"b',
    "backslash": "a\\b",
    "triple-quote": 'a"""b',
    "all-at-once": 'a\r\x7f\x01"\\b\nc',
}


#: path name -> (renders `k = <value>`, preserves the value exactly).
#:
#: `_str_assign` is the one exception to exact preservation, and it predates
#: this issue: gh-192 made its multi-line form strip leading/trailing newlines
#: so repeated saves are byte-stable, which costs a trailing newline on the
#: first read. Idempotence, not identity — asserted as such below rather than
#: quietly excluded.
def _scalar(doc: dict) -> str:
    return doc["k"]


def _first(doc: dict) -> str:
    return doc["k"][0]


#: path name -> (render `k = <value>`, pull the value back out, exact?).
ESCAPERS = {
    "_toml_inline_string": (
        lambda v: f"k = {C._toml_inline_string(v)}",
        _scalar,
        True,
    ),
    "_str_assign": (lambda v: C._str_assign("k", v), _scalar, False),
    "_toml_scalar": (lambda v: f"k = {C._toml_scalar(v)}", _scalar, True),
    "_toml_value": (lambda v: f"k = {C._toml_value(v)}", _scalar, True),
    # Found by the ratchet below on its first run — neither the issue nor I
    # knew this one existed. It was the only correct path of the five, and
    # only by accident of `json.dumps(ensure_ascii=True)`.
    "_toml_string_array": (
        lambda v: f"k = {C._toml_string_array([v])}",
        _first,
        True,
    ),
}


class TestEveryPathEmitsReadableToml:
    """The measured table, inverted: no cell may say "reject"."""

    @pytest.mark.parametrize("path", sorted(ESCAPERS))
    @pytest.mark.parametrize("kind", sorted(FORBIDDEN))
    def test_tomllib_accepts_what_was_just_written(self, path, kind):
        render, _extract, _exact = ESCAPERS[path]
        text = render(FORBIDDEN[kind])
        tomllib.loads(text)  # the assertion: it does not raise

    @pytest.mark.parametrize("path", sorted(ESCAPERS))
    @pytest.mark.parametrize("kind", sorted(FORBIDDEN))
    def test_the_value_survives(self, path, kind):
        render, extract, exact = ESCAPERS[path]
        value = FORBIDDEN[kind]
        got = extract(tomllib.loads(render(value)))
        if exact:
            assert got == value
        else:
            # gh-192 idempotence: the second write must reproduce the first
            # byte for byte, which is the property repeated saves depend on.
            assert got.strip("\n") == value.strip("\n")
            assert render(got) == render(value)


class TestTheRatchet:
    """A fifth escaper must not be able to arrive untested."""

    def test_every_escaper_is_covered(self):
        found = {
            name
            for name in dir(C)
            if (name.startswith("_toml_") or name == "_str_assign")
            and callable(getattr(C, name))
            and name
            not in {
                # The shared primitive the others now delegate to; exercised
                # through every one of them.
                "_toml_basic_string",
            }
        }
        assert found == set(ESCAPERS), (
            "an escaper in _config is not in this file's matrix: "
            f"{found ^ set(ESCAPERS)}"
        )


class TestTheSelfCheckRefusesBadOutput:
    """`_dump` caught all three bugs and handed the text back regardless."""

    def test_it_raises_rather_than_returning_unreadable_text(
        self, monkeypatch
    ):
        # Force the exact condition: a section renders to something tomllib
        # cannot read. Before gh-844 this returned the text and `save` wrote
        # it, so the failure surfaced in a later command.
        monkeypatch.setattr(C, "_toml_basic_string", lambda v: '"a\rb"')
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "codec": {"k": {"entries": ["v"]}},
        }
        with pytest.raises(ValueError, match="cannot read back"):
            C._dump(cfg)

    def test_a_good_config_still_dumps(self, tmp_path):
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "init_params": [
                    {"name": "g", "type": "double", "doc": "a\rb\x7f"}
                ],
            },
        }
        C.save(tmp_path, cfg)
        back = C.load(tmp_path)["capture"]["init_params"][0]
        assert back["doc"] == "a\rb\x7f"


class TestTheWholeManifestRoundTrips:
    """End to end, through the pair that actually loses data."""

    @pytest.mark.parametrize("kind", sorted(FORBIDDEN))
    def test_a_doc_of_any_shape_survives_save_and_load(self, tmp_path, kind):
        value = FORBIDDEN[kind]
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "methods": [{"name": "run", "doc": value}],
            },
        }
        C.save(tmp_path, cfg)
        got = C.load(tmp_path)["capture"]["methods"][0]["doc"]
        assert got.strip("\n") == value.strip("\n")
        # And the second save is byte-identical, so a manifest does not drift
        # a newline per command.
        first = (tmp_path / C.FILENAME).read_bytes()
        C.save(tmp_path, C.load(tmp_path))
        assert (tmp_path / C.FILENAME).read_bytes() == first
