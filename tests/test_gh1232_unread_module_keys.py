"""gh-1232: four module keys were accepted by the validator and read by nobody.

Three of them were a real key **one table up**, which is the worst version of
this: an author who wrote `flat_sources` on the module instead of on
`[module.X.segment]` got silence and a composer that ignored it, when `_keys`
already knew how to say which table it belongs to.

| key | what it needed to be | what actually reads that word |
|---|---|---|
| `enums` | a module-level enum registry | `C.enums(cfg)` -- project-level `[[enum]]`, no module argument |
| `stream` | `[module.X] stream` | `C.composer_stream()` reads `[module.X.composer] stream` |
| `generator` | `[module.X] generator` | `source.generates.generator` |
| `flat_sources` | `[module.X] flat_sources` | `C.composer_segment().get("flat_sources")` |

Follows gh-1190's precedent for composer `methods`: drop them from their
`*_MODULE_KEYS` sets so the key **reports**, and `Unknown.valid_for` names the
table it is valid on. Stacked on gh-1236, which is what registered those
sub-tables -- without it the message could only say "jm does not read it
anywhere", which is true of `enums` and false of the other three.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _keys as K  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402

MOD = "wfm_compose"

#: The four, with the value shape an author would plausibly write and the
#: table the message must send them to (None = it has no home anywhere).
_REMOVED = {
    "enums": ({"mode": ["a", "b"]}, None),
    "stream": ({"stream": True}, "[module.X.composer]"),
    "generator": ({"name": "g"}, "[module.X.source.generates]"),
    "flat_sources": (True, "[module.X.segment]"),
}

#: Keys that legitimately exist at BOTH module level and sub-table level, with
#: the reason. Everything else overlapping is the gh-1232 defect: a sub-table
#: key sitting in the module vocabulary, where it validates and does nothing.
#:
#: This is a ratchet. It may shrink; a new entry needs a reason written here.
_DUAL_LEVEL = {
    "doc": "a module documents itself, and so does each of its faces",
    "header": "the module's backing header vs the generator's own",
    "type_name": "the module's class vs the source / segment / timeline class",
}


def _messages(cfg: dict) -> list[str]:
    """Every unknown-key message *cfg* produces.

    Through `unknown_keys`, not `warn_unknown_keys`: the latter deduplicates
    against a module-level `_SEEN` for the life of the process, so the second
    test to provoke an identical message would see nothing and pass
    vacuously. The first cut of this file did exactly that.
    """
    return [u.message() for u in K.unknown_keys(cfg)]


def _warn(cfg: dict) -> str:
    return "\n".join(_messages(cfg))


def _composer_subtable_keys() -> set:
    out: set = set()
    for (kind, _tbl), label in K.KIND_DICT_TABLE_VOCAB.items():
        if kind == "composer":
            out |= K.KIND_KEYS[label]
    for (kind, _o, _i), label in K.KIND_NESTED_VOCAB.items():
        if kind == "composer":
            out |= K.KIND_KEYS[label]
    return out


@pytest.mark.parametrize("key", sorted(_REMOVED))
def test_it_is_no_longer_accepted_on_a_module(key: str) -> None:
    for kind, keys in K.MODULE_KEYS_BY_KIND.items():
        assert key not in keys, f"`{key}` is still a {kind} module key"


@pytest.mark.parametrize("key", sorted(_REMOVED))
def test_writing_it_on_the_module_now_reports(key: str) -> None:
    cfg = _cfg()
    cfg["module"][MOD][key] = _REMOVED[key][0]
    assert f"unknown composer module key `{key}`" in _warn(cfg)


@pytest.mark.parametrize(
    "key,table",
    sorted((k, t) for k, (_v, t) in _REMOVED.items() if t),
    ids=lambda x: str(x),
)
def test_the_message_names_the_table_it_belongs_to(
    key: str, table: str
) -> None:
    """The point of the whole exercise. "jm does not read it anywhere" is a
    confidently wrong diagnosis for a key that IS read, one table down -- the
    gh-1227 lesson, which cost more than the missing feature it stood in
    front of."""
    cfg = _cfg()
    cfg["module"][MOD][key] = _REMOVED[key][0]
    assert f"it is a {table} key" in _warn(cfg)


def test_enums_correctly_says_it_is_read_nowhere() -> None:
    """The one of the four with no home. Claiming a table for it would be the
    same wrong-diagnosis failure in the other direction."""
    cfg = _cfg()
    cfg["module"][MOD]["enums"] = {"mode": ["a"]}
    w = _warn(cfg)
    assert "jm does not read it anywhere" in w
    assert "it is a" not in w


def test_one_mistake_produces_one_message() -> None:
    """A stray key holding a TABLE used to warn twice -- once as an unknown
    key and once as a sub-table with no vocabulary. The second says the
    opposite thing about the same mistake: "jm cannot check inside this" when
    the answer is "this does not belong here"."""
    cfg = _cfg()
    cfg["module"][MOD]["stream"] = {"stream": True}
    msgs = _messages(cfg)
    assert len(msgs) == 1, msgs


def test_a_real_unwalked_subtable_still_reports() -> None:
    """...and suppressing the second message must not disarm gh-1114's check
    for a table that IS accepted and has no vocabulary for its rows."""
    cfg = _cfg()
    saved = K.KIND_DICT_TABLE_VOCAB.pop(("composer", "oo"))
    try:
        assert "has no key vocabulary" in _warn(cfg)
    finally:
        K.KIND_DICT_TABLE_VOCAB[("composer", "oo")] = saved


def test_no_subtable_key_sits_in_the_module_vocabulary() -> None:
    """The ratchet, and the gate that would have caught all three.

    A key valid on a composer sub-table has no business in the module
    vocabulary: there it validates, generates nothing, and -- because it is a
    real word elsewhere -- reads to the author as if it worked. `stream`,
    `generator` and `flat_sources` were exactly that.

    Three keys legitimately live at both levels; each is named in
    `_DUAL_LEVEL` with its reason. Anything else is this defect returning.
    """
    overlap = K.MODULE_KEYS_BY_KIND["composer"] & _composer_subtable_keys()
    unexplained = sorted(overlap - set(_DUAL_LEVEL))
    assert not unexplained, (
        f"these are composer sub-table keys sitting in the composer MODULE "
        f"vocabulary, where they validate and do nothing: {unexplained}. "
        "Either remove them (gh-1232) or add each to _DUAL_LEVEL with the "
        "reason it is legitimately a key at both levels."
    )


def test_the_ratchet_is_armed() -> None:
    """A ratchet that cannot see a violation is a description."""
    overlap = K.MODULE_KEYS_BY_KIND["composer"] & _composer_subtable_keys()
    assert overlap == set(_DUAL_LEVEL), (
        "_DUAL_LEVEL has drifted from the real overlap -- an entry that no "
        f"longer overlaps is dead: overlap={sorted(overlap)}"
    )
