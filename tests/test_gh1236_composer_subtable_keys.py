"""gh-1236: a composer's TABLE sub-tables are checked, and survive a save.

`_check_kind_module` walked a kind module's sub-tables whose rows are arrays of
tables, and reported an "unwalked sub-table" for any it had no vocabulary for --
its own comment says "adding a sub-table must not be a way back into the
silence". Its guard is `isinstance(rows, list)`, so a sub-table that is a plain
TABLE was never a candidate: a composer's `source`, `segment`, `timeline`,
`oo`, `json`, `cli` and `composer` fell out of the walk entirely.

gh-1234 is what that cost. `object` on a `source.fields` row passed in silence
and came out of the renderer as `KeyError: 'type'`, when `object` is a real key
one table over and this registry already knows how to say which. A misplaced
key is the *most likely* mistake on these tables precisely because they are
nested -- TOML binds whatever sits under the last header, so a key written two
lines too low lands here.

Measuring the vocabularies turned up the writer half too, unreported and worse
in effect than gh-1229's: a `source.fields` / `segment.fields` row was READ for
`complex`, `c_ptr`, `c_len` and `doc` and WRITTEN for none of them. `complex`
turns a complex64 stream back into a scalar; `c_ptr` / `c_len` (gh-1184) are
what let an owned array live in a nested struct member, so losing them makes
the generated C write to `<name>` / `n_<name>` against the author's own struct.
gh-1229 dropped a property; this changed emitted C.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit._keys import (  # noqa: E402
    KIND_DICT_TABLE_VOCAB,
    KIND_KEYS,
    KIND_NESTED_VOCAB,
    warn_unknown_keys,
)
from test_composer_codegen import _cfg  # noqa: E402

MOD = "wfm_compose"

#: A representative value for every key in every composer sub-table
#: vocabulary. Same contract as gh-1229's probe: a key with no representative
#: fails `test_every_accepted_key_has_a_representative`, so the gate cannot
#: fall behind `_keys`, and the fixture is checked before it is trusted
#: because a wrong shape reads as key loss.
_REPRESENTATIVE: dict[str, object] = {
    # composer field / computed rows
    "name": "iq",
    "type": "uint8_t*",
    "enum": "wfm_type",
    "default": "0",
    "bytes": True,
    "complex": True,
    "c_ptr": "sync.bits",
    "c_len": "sync.len",
    "coerce": "hex",
    "aliases": ["i"],
    "doc": 'The "IQ" run.',
    "fn": "wfm_iq",
    # source
    "object": "wfm_synth",
    "struct": "wfm_source_t",
    "type_name": "Synth",
    "fields": [{"name": "freq", "type": "double"}],
    "computed": [{"name": "dur", "type": "double", "fn": "wfm_dur"}],
    "generates": {"generator": "nco", "bridge_fn": "wfm_bridge"},
    # source.generates
    "generator": "nco",
    "bridge_fn": "wfm_bridge",
    "state_type": "nco_state_t",
    "steps_fn": "nco_steps",
    "step_fn": "nco_step",
    "reset_fn": "nco_reset",
    "destroy_fn": "nco_destroy",
    "header": "nco/nco_core.h",
    "output_type": "float complex",
    # segment
    "sources": "wfm_source_t",
    "sources_member": "srcs",
    "count_member": "n_srcs",
    "flat_sources": True,
    # timeline / oo
    "loop": ["a", "b"],
    "factories": ["mk"],
    "emit": "emit_fn",
    "discriminant": "kind",
    "composer_type_name": "Composer",
    # composer ergonomics
    "stream": True,
    "to_dict": True,
    "realtime": {"clk_new": "wfm_clk_new"},
    # json / cli
    "enabled": True,
    "to_json_fn": "wfm_to_json",
    "from_json_fn": "wfm_from_json",
    "from_file_fn": "wfm_from_file",
    "to_json_trailing": ["seed"],
}

#: `(table, vocabulary)` for every composer sub-table that is a TABLE.
_DICT_TABLES = sorted(
    (tbl, vocab)
    for (kind, tbl), vocab in KIND_DICT_TABLE_VOCAB.items()
    if kind == "composer"
)
#: `(outer, inner, vocabulary)` for the rows one level down.
_NESTED = sorted(
    (outer, inner, vocab)
    for (kind, outer, inner), vocab in KIND_NESTED_VOCAB.items()
    if kind == "composer"
)


def _full(vocab: str) -> dict:
    """Every key *vocab* accepts, carrying its representative value."""
    return {k: _REPRESENTATIVE[k] for k in sorted(KIND_KEYS[vocab])}


def _warnings(cfg: dict) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        warn_unknown_keys(cfg)
    return buf.getvalue()


def _round_trip(tmp_path: pathlib.Path, cfg: dict) -> dict:
    C.save(tmp_path, cfg)
    return C.load(tmp_path)["module"][MOD]


# ── the vocabularies are complete and non-vacuous ────────────────────────────


def test_every_accepted_key_has_a_representative() -> None:
    """A key added to any composer sub-table vocabulary is covered here.

    The only hand-written list in the file, and it cannot fall behind `_keys`
    without saying so.
    """
    missing: dict[str, list[str]] = {}
    for _tbl, vocab in _DICT_TABLES:
        gap = sorted(k for k in KIND_KEYS[vocab] if k not in _REPRESENTATIVE)
        if gap:
            missing[vocab] = gap
    for _o, _i, vocab in _NESTED:
        gap = sorted(k for k in KIND_KEYS[vocab] if k not in _REPRESENTATIVE)
        if gap:
            missing[vocab] = gap
    assert not missing, f"no representative value for: {missing}"


def test_the_walk_covers_every_dict_subtable_the_dumper_writes() -> None:
    """Registration-free in the direction that matters: a composer sub-table
    with no vocabulary is REPORTED, not skipped, so adding one cannot be a way
    back into the silence this issue is about."""
    cfg = _cfg()
    cfg["module"][MOD]["brand_new_table"] = {"anything": 1}
    assert "has no key vocabulary" in _warnings(cfg)


def test_a_clean_manifest_stays_silent(tmp_path: pathlib.Path) -> None:
    """The gate must not cry wolf on the shapes jm itself writes -- a
    validator that warns on valid input gets ignored, and then it is a gate
    that blocks nothing."""
    assert _warnings(_cfg()) == ""


# ── the reporting half (gh-1234's case) ──────────────────────────────────────


class TestAMisplacedKeyIsNamed:
    def test_object_on_a_source_field_is_reported_at_load(self) -> None:
        """gh-1234's exact input. It reached `_composer._field_fmt` as
        `KeyError: 'type'` before; now the registry names it, and names the
        tables it IS valid on -- which is the whole point of a registry."""
        cfg = _cfg()
        cfg["module"][MOD]["source"]["fields"].append(
            {"name": "frame", "object": "frame.FrameDesc"}
        )
        w = _warnings(cfg)
        assert "unknown composer field key `object`" in w
        assert "init_param" in w  # where it IS valid

    @pytest.mark.parametrize("tbl,_v", _DICT_TABLES)
    def test_a_typo_in_each_dict_subtable_is_reported(
        self, tbl: str, _v: str
    ) -> None:
        """Per table, not once: `source` being walked proves nothing about
        `oo`, which is the `_a_sweep_is_only_as_good_as_its_tree` shape."""
        cfg = _cfg()
        cfg["module"][MOD].setdefault(tbl, {})["deliberate_typo"] = "x"
        assert "deliberate_typo" in _warnings(cfg)

    @pytest.mark.parametrize("outer,inner,_v", _NESTED)
    def test_a_typo_one_level_down_is_reported(
        self, outer: str, inner: str, _v: str
    ) -> None:
        cfg = _cfg()
        tbl = cfg["module"][MOD].setdefault(outer, {})
        row = {"name": "x", "type": "double", "deliberate_typo": "y"}
        tbl[inner] = row if inner == "generates" else [row]
        assert "deliberate_typo" in _warnings(cfg)


# ── the writer half: what is accepted is preserved ───────────────────────────


class TestEveryAcceptedKeySurvivesASave:
    @pytest.mark.parametrize("tbl,vocab", _DICT_TABLES)
    def test_a_dict_subtable_round_trips(
        self, tbl: str, vocab: str, tmp_path: pathlib.Path
    ) -> None:
        cfg = _cfg()
        cfg["module"][MOD][tbl] = _full(vocab)
        back = _round_trip(tmp_path, cfg)[tbl]
        lost = sorted(k for k in _full(vocab) if k not in back)
        assert not lost, f"[module.X.{tbl}] keys dropped by C.save: {lost}"

    @pytest.mark.parametrize("outer,inner,vocab", _NESTED)
    def test_a_nested_row_round_trips(
        self, outer: str, inner: str, vocab: str, tmp_path: pathlib.Path
    ) -> None:
        """The half that was actually broken. A field row was read for
        `complex`, `c_ptr`, `c_len` and `doc` and written for none of them."""
        row = _full(vocab)
        cfg = _cfg()
        cfg["module"][MOD].setdefault(outer, {})[inner] = (
            row if inner == "generates" else [row]
        )
        got = _round_trip(tmp_path, cfg)[outer][inner]
        got = got if isinstance(got, dict) else got[0]
        lost = sorted(k for k in row if k not in got)
        assert not lost, f"{outer}.{inner} keys dropped by C.save: {lost}"

    def test_the_probe_declares_nothing_jm_rejects(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The fixture is checked before it is trusted. A wrong shape reads as
        key loss, which is the error rate gh-1229 measured the hard way."""
        cfg = _cfg()
        for tbl, vocab in _DICT_TABLES:
            cfg["module"][MOD][tbl] = _full(vocab)
        _round_trip(tmp_path, cfg)
        assert "unknown" not in _warnings(cfg)


def test_the_field_keys_gh1234_names_come_from_the_vocabulary() -> None:
    """gh-1234's refusal lists the keys jm does NOT read on a composer field.
    That list is this vocabulary -- two copies would disagree the first time a
    key was added, and the message would then name a key as unread that the
    validator accepts.

    Asserted against the SOURCE, not with `is`. The first cut of this compared
    the objects, and `frozenset(x)` returns `x` unchanged for a frozenset --
    so the sabotage that was supposed to prove it armed passed instead. An
    equality check is no better: a literal copy that happens to agree today is
    exactly the state this forbids. What has to be true is that `_composer`
    does not spell the set at all.
    """
    import re

    src = (pathlib.Path(C.__file__).parent / "_composer.py").read_text(
        encoding="utf-8"
    )
    assign = [
        ln for ln in src.splitlines() if re.match(r"_FIELD_KEYS\s*=", ln)
    ]
    assert len(assign) == 1, f"expected one _FIELD_KEYS assignment: {assign}"
    assert assign[0].strip() == "_FIELD_KEYS = _keys.COMPOSER_FIELD_KEYS", (
        "`_composer._FIELD_KEYS` must BE the registry's set, not a copy of "
        f"it: {assign[0].strip()!r}"
    )


def test_a_complex_field_stays_complex_through_a_save(
    tmp_path: pathlib.Path,
) -> None:
    """The named instance, spelled out. gh-1229 dropped a property; this
    changed the shape the binding marshals -- a complex64 stream came back as
    a scalar, and gh-1184's nested-member overrides came back as `<name>` /
    `n_<name>` against the author's own struct."""
    cfg = _cfg()
    cfg["module"][MOD]["source"]["fields"].append(
        {
            "name": "iq",
            "type": "uint8_t*",
            "complex": True,
            "c_ptr": "sync.bits",
            "c_len": "sync.len",
        }
    )
    back = _round_trip(tmp_path, cfg)
    got = next(f for f in back["source"]["fields"] if f["name"] == "iq")
    assert got.get("complex") is True
    assert got.get("c_ptr") == "sync.bits"
    assert got.get("c_len") == "sync.len"
