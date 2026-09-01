"""gh-1229: every key a `[module.X]` face accepts survives `save` / `load`.

`_keys` decides which keys a kind module accepts and `_config._dump` decides
which it writes back. Those are two answers to one question, and for twenty
keys they disagreed: the validator said yes, the writer had no branch, and the
key was gone from the file the command had just rewritten.

gh-794's `capsule` was the sharpest instance. It is what makes a handle publish
`_capsule` at all, so a handle another module borrows a pointer from stopped
publishing it on the next mutating command, and the consumer's
`PyCapsule_GetPointer` began failing against C nobody had touched. Nothing
warned. It also means a hand-written manifest works right up until the first
`jm` command rewrites it.

The gate has to derive its key list from the code under test or it goes stale
the first time a key is added, which is how this arrived. Two things the
gh-838 init-param probe did not have to handle:

* **Row shape matters.** A wrong row shape raises or is rejected rather than
  round-tripping, and an empty list is legitimately indistinguishable from an
  absent key -- a naive probe reports both as key loss. So every key gets a
  *representative* value, `test_every_accepted_key_has_a_representative`
  requires one for each, and `test_the_probe_declares_nothing_jm_rejects`
  proves the representatives are shapes jm actually accepts.
* **Per kind.** `handle`, `capsule` and `composer` have different
  vocabularies, so one probe over one kind proves nothing about the others.
"""

from __future__ import annotations

import pathlib

import pytest

from just_makeit import _config as C
from just_makeit._keys import MODULE_KEYS_BY_KIND

#: A value for every key in every face's vocabulary. Shapes are the ones
#: `_keys` validates -- a wrong one is a probe defect that reads as key loss,
#: which is why `test_the_probe_declares_nothing_jm_rejects` exists.
_REPRESENTATIVE: dict[str, object] = {
    # -- shared ------------------------------------------------------------
    "backing": "ring",
    "package": "pkg",
    "header": "p/ring.h",
    "depends_on": ["other"],
    "extra_link_libs": ["m"],
    "extra_include_dirs": ["inc"],
    "extra_types": ["ring_t"],
    "doc": "A ring.",
    "no_generate": "true",
    "reexports": {"sub": ["Name"]},
    "capsule_name": "p.ringcap",
    "functions": [{"name": "f", "return_type": "int"}],
    "functions_in_core": "true",
    "serializable": "true",
    "optional_backend": "true",
    "init_params": [{"name": "n", "type": "int"}],
    "properties": [{"name": "pr", "type": "int"}],
    "enums": {"mode": ["a", "b"]},
    # -- handle ------------------------------------------------------------
    "handle_type": "ring_t *",
    "type_name": "Ring",
    "create_fn": "ring_open",
    "init_fn": "ring_init",
    "create_args": [{"name": "path", "type": "const char *"}],
    "create_post": [{"fn": "ring_post"}],
    "create_error": "RingError",
    "create_error_message": "cannot open",
    "close_fn": "ring_close",
    "close_returns": "int",
    "context_manager": True,
    "getters": [
        {
            "fn": "ring_get",
            "out": "ring_info_t",
            "fields": [{"name": "n", "type": "int"}],
        }
    ],
    "factories": [{"name": "from_path", "create_fn": "ring_from_path"}],
    "capsule": "p.ring",
    # -- capsule -----------------------------------------------------------
    "destroy_fn": "ring_destroy",
    # -- composer ----------------------------------------------------------
    "extra_methods": [{"name": "em", "fn": "c_em"}],
    "composer": {"stream": True},
    "composes": ["nco"],
    "sample_type": True,
    "source": {"object": "src", "struct": "src_t"},
    "segment": {"flat_sources": True},
    "timeline": {"type_name": "Timeline"},
    "oo": {"factories": ["mk"]},
    "json": {"enabled": True},
    "cli": {"enabled": True},
    "serializers": [{"name": "s", "fn": "c_s"}],
    "stream": {"stream": True},
    "generator": {"name": "g"},
    "flat_sources": True,
    "settings": [{"name": "st", "setter_fn": "c_set", "type": "int"}],
}

#: `methods` is the one key whose row shape differs per face -- a handle
#: method spells its signature `args` / `returns`, a capsule method
#: `arg_type` / `return_type` (`_keys` splits the vocabulary for exactly
#: this reason). A composer has no `methods` key at all (gh-1190).
_PER_KIND: dict[str, dict[str, object]] = {
    "handle": {
        "methods": [
            {
                "name": "m",
                "fn": "ring_m",
                "returns": "int",
                "args": [{"name": "a", "type": "int"}],
            }
        ]
    },
    "capsule": {
        "methods": [{"name": "m", "arg_type": "float", "return_type": "float"}]
    },
    "composer": {},
}


def _module_table(kind: str) -> dict:
    """Every key *kind* accepts, each carrying its representative value."""
    reps = {**_REPRESENTATIVE, **_PER_KIND[kind]}
    table: dict = {"kind": kind}
    for key in sorted(MODULE_KEYS_BY_KIND[kind]):
        if key != "kind":
            table[key] = reps[key]
    return table


def _round_trip(tmp_path: pathlib.Path, table: dict) -> dict:
    """`save` then `load` a one-module manifest, returning the module back."""
    cfg = {
        "project": {"name": "p", "version": "0.1.0"},
        "module": {"ring": table},
    }
    C.save(tmp_path, cfg)
    return C.load(tmp_path)["module"]["ring"]


def test_every_accepted_key_has_a_representative() -> None:
    """A key added to a face's vocabulary is covered here, or this fails.

    This is what keeps the probe from going stale. `_REPRESENTATIVE` is the
    only hand-written list in the file, and it cannot fall behind `_keys`
    without saying so.
    """
    for kind, keys in MODULE_KEYS_BY_KIND.items():
        reps = {**_REPRESENTATIVE, **_PER_KIND[kind]}
        missing = sorted(k for k in keys if k != "kind" and k not in reps)
        assert not missing, (
            f"{kind} module keys with no representative value: {missing} -- "
            "add one to _REPRESENTATIVE (or _PER_KIND if its row shape "
            "differs per face) so the round-trip gate covers them"
        )


@pytest.mark.parametrize("kind", sorted(MODULE_KEYS_BY_KIND))
def test_the_probe_declares_nothing_jm_rejects(
    kind: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    """The representatives are shapes jm accepts, not shapes it warns about.

    A wrong row shape is silently dropped or reported, and either reads as key
    loss in the test below -- the exact error rate that made this issue's first
    sweep over-report by five keys. So the fixture is checked before it is
    trusted.
    """
    _round_trip(tmp_path, _module_table(kind))
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "unknown" not in combined, (
        f"the {kind} representatives are not a shape jm accepts:\n{combined}"
    )


@pytest.mark.parametrize("kind", sorted(MODULE_KEYS_BY_KIND))
def test_every_accepted_key_survives_a_save(
    kind: str, tmp_path: pathlib.Path
) -> None:
    """What the validator accepts, the writer preserves.

    Not "what a generator reads" -- a key with no reader yet is still the
    author's declaration, and dropping it makes the manifest lie about what
    they wrote. The two answers come from one declaration now
    (`MODULE_KEYS_BY_KIND`), so this cannot drift back.
    """
    table = _module_table(kind)
    back = _round_trip(tmp_path, table)
    lost = sorted(k for k in table if k not in back)
    assert not lost, f"{kind} module keys dropped by C.save: {lost}"


@pytest.mark.parametrize("kind", sorted(MODULE_KEYS_BY_KIND))
def test_every_accepted_key_keeps_its_value(
    kind: str, tmp_path: pathlib.Path
) -> None:
    """Surviving is not enough -- a preserved key must mean what it meant.

    Kept separate from the presence check so a value that round-trips as the
    wrong thing is not masked by the louder "the key is gone" failure.
    """
    table = _module_table(kind)
    back = _round_trip(tmp_path, table)
    changed = {
        k: (v, back[k]) for k, v in table.items() if k in back and back[k] != v
    }
    assert not changed, f"{kind} module keys changed value: {changed}"


def test_a_handle_still_publishes_its_capsule_after_a_save(
    tmp_path: pathlib.Path,
) -> None:
    """gh-1229's named instance, read through the accessor gh-794 generates
    from -- so the regression is reported in the vocabulary of the feature it
    breaks, not as one row of a twenty-key sweep.
    """
    table = {
        "kind": "handle",
        "backing": "ring",
        "handle_type": "ring_t *",
        "type_name": "Ring",
        "create_fn": "ring_open",
        "close_fn": "ring_close",
        "capsule": "p.ring",
    }
    back_cfg = {
        "project": {"name": "p", "version": "0.1.0"},
        "module": {"ring": table},
    }
    C.save(tmp_path, back_cfg)
    reloaded = C.load(tmp_path)
    assert C.handle_capsule(reloaded, "ring") == "p.ring"
