"""gh-2705 -- an init param's `optional` is a flag: `"false"` means false.

Every reader of the key tested it for truth. TOML's `optional = "false"` is
the string `"false"`, which is truthy, so on an array init param it turned
array dispatch (gh-1004) ON -- the opposite of what the author wrote -- and
`apply` refused the row for naming no `create_fn`.

The key is now read through `_keys.truthy`, the one reading of a flag: a
TOML bool or the strings `"true"` / `"false"`. The
tuple `_config._project_init_params` builds carries that reading to every
generator, and `_keys.flag_errors` (asked by `_config.load`, gh-2120)
refuses any other value, naming the row -- as it does for every flag.

The end-to-end check is registration-free over readers: it renders the
whole project once per spelling and requires the trees to be identical, so
a reader added later that tests the raw value for truth fails here without
this file naming it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._context._parse import (  # noqa: E402
    init_param_optional_errors,
)
from just_makeit._keys import flag_errors  # noqa: E402

#: Values a hand-written manifest could carry that are neither a TOML bool
#: nor its string form. `1` and `0` matter: `1 == True` in Python, so a
#: membership test against the bools would accept them.
_REFUSED = ["maybe", "True", "FALSE", "yes", "", 1, 0, 1.5, [], {}]


def _cfg(value, typ="float[]") -> dict:
    row = {"name": "taps", "type": typ, "optional": value}
    return {
        "w": {
            "init_params": [row],
            "views": [{"name": "v", "init_params": [dict(row)]}],
        }
    }


@pytest.mark.parametrize("value", _REFUSED, ids=repr)
def test_a_value_that_is_not_a_flag_is_refused(value):
    # gh-2120: the one refusal of a value that is not a flag. "True" is
    # named as the spelling an older jm wrote, with its repair.
    errs = flag_errors(_cfg(value))
    assert [e.split(": optional = ")[0] for e in errs] == [
        "w.init_params[taps]",
        "w.views[v].init_params[taps]",
    ]
    tail = "upgrade" if value == "True" else "must be true or false"
    assert all(tail in e for e in errs), errs


@pytest.mark.parametrize("value", [False, "false"], ids=repr)
def test_false_is_absence_even_on_a_scalar(value):
    # Not truthy, so the gh-2697 refusal (dispatch on a non-array) is not
    # asked: `"false"` is no more a declaration than `false`.
    assert init_param_optional_errors(_cfg(value, typ="int")) == []


@pytest.mark.parametrize("value", [True, "true"], ids=repr)
def test_true_is_dispatch_in_either_spelling(value):
    assert len(init_param_optional_errors(_cfg(value, typ="int"))) == 2
    assert init_param_optional_errors(_cfg(value)) == []


def _render(tmp_path: Path, tag: str, optional: str, extra: str) -> dict:
    """Every file `apply` writes for object `w`, keyed by relative path,
    with `optional = <optional>` on its array init param."""
    base = tmp_path / tag
    base.mkdir()
    r = run_cli("new", "p", cwd=base)
    assert r.returncode == 0, r.stderr
    proj = base / "p"
    manifest = proj / "just-makeit.toml"
    with manifest.open("a", encoding="utf-8") as f:
        f.write(
            '\n[w]\nno_step = "true"\nclass_name = "W"\n\n'
            '[[w.init_params]]\nname = "taps"\ntype = "float[]"\n'
            f"optional = {optional}\n{extra}"
        )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return {
        str(p.relative_to(proj)): p.read_bytes()
        for p in sorted(proj.rglob("*"))
        if p.is_file() and p.name != "just-makeit.toml"
    }


@pytest.mark.parametrize(
    ("as_bool", "as_str", "extra"),
    [
        # The issue's manifest: no `create_fn`, which dispatch would need.
        ("false", '"false"', ""),
        ("true", '"true"', 'create_fn = "w_create_taps"\n'),
    ],
    ids=["false", "true"],
)
def test_the_string_renders_what_the_bool_renders(
    tmp_path, as_bool, as_str, extra
):
    want = _render(tmp_path, "bool", as_bool, extra)
    got = _render(tmp_path, "str", as_str, extra)
    assert got.keys() == want.keys()
    differ = [k for k in want if got[k] != want[k]]
    assert differ == [], differ
