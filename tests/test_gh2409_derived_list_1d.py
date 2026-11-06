"""gh-2409: `derived` as a one-name list on a 1-D array names its length.

gh-900's `derived = "nx"` names a 1-D array init-param's length, and
gh-1097 let the same key be a LIST naming a 2-D array's extents. Only the
2-D branch read the list: on a 1-D array `derived = ["nx"]` reached the
prototype as ``size_t ['nx']`` and `jm apply` crashed in `_ctorsig`. doppler
hit it on a view (doppler#2666) and renamed its C parameter to dodge it.

Both spellings now go through the one reading of the key
(`_context._state._ctor_extent_names`): a list naming as many extents as
the array has, or a bare string. A list naming more is refused, naming the
param. `jm script` spells a one-name list the way the CLI takes it, rather
than formatting the Python list into the flag.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

from pathlib import Path

import pytest

from _jmrun import run_cli

PROTO = "size_t nx, const float *code"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _project(tmp_path: Path, on: str) -> Path:
    """A 1-D `derived` init-param on the object itself, or on its view."""
    _ok("new", "pp", cwd=tmp_path)
    root = tmp_path / "pp"
    _ok("module", "m", cwd=root)
    spec = "code:float[]:derived:nx"
    if on == "object":
        _ok("object", "rc", "--module", "m", "--init-param", spec, cwd=root)
    else:
        _ok("object", "rc", "--module", "m", "--state", "g:double:1", cwd=root)
        _ok(
            "view", "rc", "MRC", "--module", "m",
            "--create-fn", "pp_rc_create_code", "--init-param", spec,
            cwd=root,
        )  # fmt: skip
    return root


def _respell(root: Path, derived: str) -> None:
    frag = root / "objects" / "rc.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count('derived = "nx"') == 1, text
    frag.write_text(
        text.replace('derived = "nx"', f"derived = {derived}"),
        encoding="utf-8",
    )


@pytest.mark.parametrize("on", ["object", "view"])
def test_a_one_name_list_names_the_length(tmp_path, on):
    root = _project(tmp_path, on)
    header = root / INC_ROOT / "rc" / "rc_core.h"
    assert PROTO in header.read_text(encoding="utf-8"), "the string form"
    _respell(root, '["nx"]')
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, (r.stdout, r.stderr)
    text = header.read_text(encoding="utf-8")
    assert PROTO in text
    assert "['nx']" not in text


@pytest.mark.parametrize("on", ["object", "view"])
def test_a_list_naming_two_extents_of_a_1d_array_is_refused(tmp_path, on):
    root = _project(tmp_path, on)
    _respell(root, '["ny", "nx"]')
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0
    assert "init_param 'code'" in r.stderr, r.stderr
    assert "1-D" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_the_script_spells_a_one_name_list_as_the_cli_takes_it(tmp_path):
    root = _project(tmp_path, "view")
    _respell(root, '["nx"]')
    out = run_cli("script", cwd=root)
    assert out.returncode == 0, out.stderr
    assert "code:float[]:derived:nx" in out.stdout, out.stdout
    assert "['nx']" not in out.stdout


def test_the_script_notes_a_2d_extent_list_it_cannot_spell():
    """No CLI flag names a 2-D array's extents, so the script says so.

    It used to format the list into the flag, `derived:['ny', 'nx']`, which
    replays as a 1-D length named `['ny', 'nx']`.
    """
    from just_makeit._script import _init_param_carry, _param_notes

    row = {"name": "ref", "type": "float[][]", "derived": ["ny", "nx"]}
    spec, carried = _init_param_carry(row)
    assert spec == "ref:float[][]"
    notes = "".join(_param_notes("init-param", row, carried, "--init-param"))
    assert 'derived = ["ny", "nx"]' in notes
