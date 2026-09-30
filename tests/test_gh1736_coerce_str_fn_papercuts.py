"""gh-1736: three papercuts in gh-1709's `coerce_str_fn` output.

Found by doppler's dry run, whose composer source has four `bit_pattern`
fields all naming one host reader, `dp_wfm_field_bits`:

1. jm emitted one `_coerce_<field>` helper per field -- four byte-identical
   copies. The helper is named by the FUNCTION now, one per distinct
   function, and every field naming it calls that one.
2. jm's own str grammar (`0`/`1` digits, `0x` hex) was still emitted in
   `_attach_bytes` when every `bit_pattern` field named a host reader: dead
   code, and the second grammar gh-1709 exists to remove. It is emitted only
   while some field reads it, decided by `uses_builtin_bit_grammar` alone.
3. A comment in `<cname>_bridge.h` interpolated the field list and ran to
   104 columns, past jm's 79. No gate held generated composer C to a width,
   so the class check here renders every composer file for a fixture with
   every seam shape -- `coerce_str_fn`, gh-1711's owned pointer, a bridge_fn
   with its error fn, computed properties -- and holds its comment lines to
   79 columns, and every line of the bridge header, which already fits.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402
from test_gh1711_composer_owned_pointer import FRAME  # noqa: E402

MOD = "wfm_compose"
HOST = "dp_wfm_field_bits"
#: jm's own grammar's refusal: present exactly when the grammar is emitted.
GRAMMAR = "bit string must be 0/1 or '0x..' hex"
#: doppler's four pattern fields, which is what made the comment 104 wide.
PATTERNS = ("bits", "pilot", "preamble", "sync_word")


def _fields(*rows: dict) -> dict:
    """The shared composer cfg, its `bits` field replaced by *rows*."""
    cfg = copy.deepcopy(_cfg())
    fs = cfg["module"][MOD]["source"]["fields"]
    fs[:] = [f for f in fs if f["name"] != "bits"] + list(rows)
    return cfg


def _pattern(name: str, fn: str = "") -> dict:
    row = {
        "name": name,
        "type": "uint8_t*",
        "bytes": True,
        "coerce": "bit_pattern",
    }
    if fn:
        row["coerce_str_fn"] = fn
    return row


def _helpers(src: str) -> "list[str]":
    """Each `_coerce_*` helper DEFINED in *src* (its definition line)."""
    return re.findall(r"^(_coerce_\w+)\(uint8_t \*\*dst", src, re.M)


def _full() -> dict:
    """Every seam and field shape the bridge header and `_ext.c` render."""
    cfg = _fields(*(_pattern(n, HOST) for n in PATTERNS), dict(FRAME))
    src = cfg["module"][MOD]["source"]
    src["generates"] = {
        "generator": "wfm_synth",
        "bridge_fn": "wfm_synth_from_source",
        "bridge_error_fn": "wfm_synth_from_source_error",
    }
    src["computed"] = [
        {"name": "duration", "type": "double", "fn": "wfm_source_duration"}
    ]
    cfg["module"][MOD]["cli"] = {"enabled": True}
    return cfg


class TestOneHelperPerFunction:
    def test_two_fields_naming_one_fn_share_one_helper(self) -> None:
        cfg = _fields(_pattern("bits", HOST), _pattern("pilot", HOST))
        s = _composer.render_source_type(cfg, MOD)
        assert _helpers(s) == [f"_coerce_{HOST}"]
        for n in ("bits", "pilot"):
            # the constructor and the setter both call the shared helper
            assert (
                f"_coerce_{HOST}(&self->src.{n}, &self->src.n_{n}, {n})" in s
            )
            assert (
                f"return _coerce_{HOST}(&self->src.{n}, &self->src.n_{n}, "
                "value)" in s
            )

    def test_two_distinct_fns_get_two_helpers(self) -> None:
        cfg = _fields(_pattern("bits", HOST), _pattern("pilot", "other_fn"))
        s = _composer.render_source_type(cfg, MOD)
        assert _helpers(s) == [f"_coerce_{HOST}", "_coerce_other_fn"]
        assert "_coerce_other_fn(&self->src.pilot" in s
        assert f"_coerce_{HOST}(&self->src.bits" in s


class TestTheBuiltinGrammarOnlyWhenRead:
    def test_absent_when_every_bit_pattern_field_names_a_host_fn(
        self,
    ) -> None:
        cfg = _fields(_pattern("bits", HOST), _pattern("pilot", "other_fn"))
        s = _composer.render_source_type(cfg, MOD)
        assert GRAMMAR not in s
        assert "0/1 string" not in s
        # bytes and int sequences are still taken, for every host field
        assert "PySequence_Fast(" in s
        assert "memcpy(buf, PyBytes_AS_STRING(obj), (size_t)nb);" in s

    def test_present_when_one_field_names_none(self) -> None:
        cfg = _fields(_pattern("bits", HOST), _pattern("pilot"))
        s = _composer.render_source_type(cfg, MOD)
        assert GRAMMAR in s

    def test_the_predicate_is_the_decider(self) -> None:
        assert _composer.uses_builtin_bit_grammar(
            [_pattern("bits", HOST), _pattern("pilot")]
        )
        assert not _composer.uses_builtin_bit_grammar(
            [_pattern("bits", HOST), {"name": "raw", "bytes": True}]
        )


# -- the class check: generated composer C stays within 79 columns ---------

_WIDTH = 79


def _comment_lines(text: str) -> "list[str]":
    """The lines of C *text* that are comment -- begin one, or sit inside a
    block comment. A trailing comment on a code line is code: the check is
    about the prose jm writes, and a code line's width is its own finding."""
    out, inside = [], False
    for line in text.splitlines():
        body = line.strip()
        if inside or body.startswith(("/*", "//")):
            out.append(line)
        if body.startswith("//"):
            continue
        if inside:
            inside = "*/" not in body
        elif body.startswith("/*"):
            inside = "*/" not in body[2:]
    return out


def _renders(cfg: dict) -> "dict[str, str]":
    return {
        "bridge.h": _composer.render_bridge_h(cfg, MOD),
        "ext.c": _composer.render_ext(cfg, MOD),
        "json": _composer.render_json_funcs(cfg, MOD),
        "cli.c": _composer.render_cli(cfg, MOD),
    }


def test_the_fixture_reaches_every_seam() -> None:
    """A width check over a header missing a seam shape proves nothing for
    that shape -- gh-1709's comment was never rendered by any fixture."""
    h = _composer.render_bridge_h(_full(), MOD)
    for fn in (HOST, "frame_parse", "wfm_synth_from_source_error"):
        assert fn in h
    assert "wfm_source_duration" in h
    assert "`bits`, `pilot`, `preamble`, `sync_word`" in h


@pytest.mark.parametrize("name", ["bridge.h", "ext.c", "json", "cli.c"])
def test_generated_comment_lines_fit(name: str) -> None:
    text = _renders(_full())[name]
    assert text
    wide = [ln for ln in _comment_lines(text) if len(ln) > _WIDTH]
    assert not wide, "\n".join(f"{len(ln)}: {ln}" for ln in wide)


def test_every_bridge_header_line_fits() -> None:
    """The header is prototypes and comments; all of it fits today, so all
    of it is held. `_ext.c`, the JSON glue and the CLI still carry wide CODE
    lines (argument lists, kwlists), which are checked as comments only."""
    h = _composer.render_bridge_h(_full(), MOD)
    wide = [ln for ln in h.splitlines() if len(ln) > _WIDTH]
    assert not wide, "\n".join(f"{len(ln)}: {ln}" for ln in wide)


def test_a_long_module_name_still_fits() -> None:
    """The banners interpolate the module name, so its width is theirs."""
    cfg = _full()
    long = "a_rather_long_waveform_composer_module_name"
    cfg["module"][long] = cfg["module"].pop(MOD)
    h = _composer.render_bridge_h(cfg, long)
    e = _composer.render_ext(cfg, long)
    for text in (h, e):
        wide = [ln for ln in _comment_lines(text) if len(ln) > _WIDTH]
        assert not wide, "\n".join(wide)
