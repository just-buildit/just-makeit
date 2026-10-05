"""Derived doc blocks are not declarations, so their text is not a name.

gh-1932. `C.declared_names` finds every declared name by walking the whole
config for a `name` key with a string value, and `C.require_declared_names`
gates every manifest write on the result. The walk descended into
`_doc_blocks`, which `_object._regenerate_module` attaches to the config for
the render chain: a table mapping each documented C struct member to its
Doxygen text. A member called `name` -- which is very common C -- therefore
produced `{"name": 'e.g. "agc.gain_db".'}`, and the gate refused the write:

    error: 'e.g. "agc.gain_db".' is not a valid dp tlm probe t name.
    Declared at RateConverter._doc_blocks.<struct_members>.dp_tlm_probe_t.name

`jm adopt` printed its verdict and then exited 1 with nothing written.
`_strip_private` already states the rule: a `_`-prefixed key is runtime state
that `load` or a command attaches, "not part of what the manifest declares".

The first case is the bug. The second is the control that matters: skipping the
doc blocks must not have turned the walk off, so a genuinely bad declared name
sitting NEXT TO a `_doc_blocks` is still refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C

DOC_TEXT = 'e.g. "agc.gain_db".'


def _blocks(member_doc: str = DOC_TEXT) -> dict:
    """What `_regenerate_module` stashes: a struct member `name`, documented."""
    return {"<struct_members>": {"probe_t": {"name": member_doc}}}


def test_a_documented_struct_member_called_name_is_not_a_declaration():
    cfg = {
        "project": {"name": "p"},
        "obj": {
            "state": [{"name": "gain", "type": "double"}],
            "_doc_blocks": _blocks(),
        },
    }
    declared = [name for _, name, _ in C.declared_names(cfg)]
    assert DOC_TEXT not in declared, declared
    assert "gain" in declared, declared  # the real declaration is still found
    C.require_declared_names(cfg)  # must not raise


def test_a_bad_declared_name_beside_doc_blocks_is_still_refused(capsys):
    """The walk is not off: only the derived table is skipped."""
    cfg = {
        "project": {"name": "p"},
        "obj": {"state": [{"name": "gaïn"}], "_doc_blocks": _blocks()},
    }
    with pytest.raises(SystemExit) as exc:
        C.require_declared_names(cfg)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "gaïn" in err
    assert "obj.state[0].name" in err
    assert "_doc_blocks" not in err


@pytest.mark.parametrize("key", ["_doc_blocks", "_group", "_anything"])
def test_every_underscore_key_is_transient_not_declared(key):
    """`_strip_private`'s rule, applied at every depth the walk reaches."""
    cfg = {
        "project": {"name": "p"},
        "obj": {"methods": [{"name": "go", key: {"name": "not an ident!"}}]},
    }
    declared = [name for _, name, _ in C.declared_names(cfg)]
    assert "not an ident!" not in declared, declared
    assert "go" in declared, declared


def test_the_non_ascii_report_ignores_doc_text_too():
    """`non_ascii_names` is the same walk: a doc comment saying `µs` is prose."""
    cfg = {
        "project": {"name": "p"},
        "obj": {
            "state": [{"name": "gain"}],
            "_doc_blocks": _blocks("period in µs"),
        },
    }
    assert C.non_ascii_names(cfg) == []
