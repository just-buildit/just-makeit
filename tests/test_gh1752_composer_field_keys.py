"""gh-1752: a project declares the keys its own generator reads off composer
field rows, and jm stops warning that they have no effect -- there only.

doppler generates wfmgen's CLI table and JSON schema from ``cli``, ``json``,
``metavar`` ... on its composer ``source.fields`` / ``segment.fields`` rows.
jm reads none of them, so every one warned on every apply as a key "jm does
not read", ~20 lines per run, and a misspelt jm key in the same list looked
exactly like the expected noise.

``[project] composer_field_keys`` names them once. The bar these tests hold:

- a declared key is silent on a source field AND a segment field row;
- an undeclared key on the same row still warns -- a typo stays visible;
- a declared key on any OTHER table still warns -- the list is not global;
- a listed name jm itself reads is refused at load, as is a malformed list;
- the declaration and the keys survive a save.
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
    project_field_key_errors,
    project_field_keys,
    unknown_keys,
)
from test_composer_codegen import _cfg  # noqa: E402

MOD = "wfm_compose"
OWN = ["cli", "json", "metavar"]


def _declared(own: "list[str] | None" = None) -> dict:
    """The composer fixture, with project keys on one row of each field
    table and ``own`` declared (``None``: no declaration at all)."""
    cfg = _cfg()
    src = cfg["module"][MOD]["source"]["fields"][0]
    seg = cfg["module"][MOD]["segment"]["fields"][0]
    src.update(cli="--freq", json="freq", metavar="HZ")
    seg.update(cli="--count", json="count")
    if own is not None:
        cfg.setdefault("project", {})["composer_field_keys"] = own
    return cfg


def _keys_reported(cfg: dict) -> "list[tuple[str, str]]":
    return [(u.kind, u.key) for u in unknown_keys(cfg)]


def test_the_fixture_warns_without_a_declaration() -> None:
    """Non-vacuous: the same rows, undeclared, report every project key on
    both field tables -- so the silence below is the declaration's doing."""
    got = _keys_reported(_declared(None))
    assert ("composer source field", "cli") in got
    assert ("composer source field", "metavar") in got
    assert ("composer field", "cli") in got
    assert ("composer field", "json") in got


def test_a_declared_key_is_silent_on_both_field_tables() -> None:
    assert _keys_reported(_declared(OWN)) == []


def test_an_undeclared_key_on_the_same_row_still_warns() -> None:
    """gh-1752's whole point: a misspelt jm key stays visible beside the
    declared ones."""
    cfg = _declared(OWN)
    cfg["module"][MOD]["source"]["fields"][0]["coerce_str_f"] = "pat_parse"
    assert _keys_reported(cfg) == [("composer source field", "coerce_str_f")]


def test_a_declared_key_on_another_table_still_warns() -> None:
    """The list extends the FIELD rows only. ``cli`` written on the source
    table itself is a misplaced key, and says so."""
    cfg = _declared(OWN)
    cfg["module"][MOD]["source"]["cli"] = "--oops"
    assert ("[module.X.source]", "cli") in _keys_reported(cfg)


def test_the_message_it_replaces_is_the_one_the_issue_quotes() -> None:
    """The undeclared warning is the gh-1236 line the issue reports, so the
    declaration is what removes it rather than some rewording."""
    (msg,) = [
        u.message()
        for u in unknown_keys(_declared(None))
        if u.key == "metavar"
    ]
    assert "unknown composer source field key `metavar`" in msg
    assert "no effect" in msg


class TestTheDeclarationIsRefusedWhenWrong:
    def test_a_key_jm_reads_is_refused(self) -> None:
        (err,) = project_field_key_errors(
            {"project": {"composer_field_keys": ["cli", "doc"]}}
        )
        assert "`doc`" in err and "jm reads" in err

    def test_a_source_only_key_jm_reads_is_refused(self) -> None:
        """``copy_fn`` is read on a SOURCE field only; it is still a jm key
        on a row the list applies to."""
        (err,) = project_field_key_errors(
            {"project": {"composer_field_keys": ["copy_fn"]}}
        )
        assert "`copy_fn`" in err

    @pytest.mark.parametrize("value", ["cli", ["cli", 3], {"cli": 1}])
    def test_a_malformed_value_is_refused(self, value: object) -> None:
        (err,) = project_field_key_errors(
            {"project": {"composer_field_keys": value}}
        )
        assert "must be a list of key names" in err

    @pytest.mark.parametrize("value", ["cli", {"cli": 1}])
    def test_a_malformed_value_declares_nothing(self, value: object) -> None:
        """Read outside `load` (which refuses it first), a value that is not
        a list declares no key rather than every character of a string."""
        cfg = _declared(None)
        cfg["project"] = {"composer_field_keys": value}
        assert project_field_keys(cfg) == frozenset()
        assert ("composer source field", "cli") in _keys_reported(cfg)

    def test_load_refuses_it(self, tmp_path: pathlib.Path) -> None:
        C.save(tmp_path, _declared(["cli", "doc"]))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
            C.load(tmp_path)
        assert "composer_field_keys lists `doc`" in err.getvalue()


def test_the_declaration_and_the_keys_survive_a_save(
    tmp_path: pathlib.Path,
) -> None:
    C.save(tmp_path, _declared(OWN))
    cfg = C.load(tmp_path)
    assert cfg["project"]["composer_field_keys"] == OWN
    src = cfg["module"][MOD]["source"]["fields"][0]
    assert (src["cli"], src["json"], src["metavar"]) == (
        "--freq",
        "freq",
        "HZ",
    )
    assert _keys_reported(cfg) == []
