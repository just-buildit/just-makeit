"""gh-2709: ``default_raw`` on a parameter that does not read it is refused.

``default_raw`` is an init-param's key (gh-1099). A method's or a module
function's parameter takes a named C constant as ``default`` itself
(gh-2679). Written on a method param, the constructor's key was reported as
unknown -- one advisory ``warning`` on stderr, exit 0 -- and otherwise
ignored, so the parameter rendered with no default: the stub lost its
``= ...``, the parse format moved it before the ``|``, and an optional
argument became a required one. Doppler re-rendered ``Ofdm.add_run`` that
way and read no diagnostic in the output (doppler-dsp/doppler#3199).

gh-2679's gate asked ``_keys.unknown_keys`` whether the warning carried the
hint, which it did: it tested the message, not the outcome, so an exit-0
apply that broke the signature passed it.

GATE: the doppler shape -- an inline ``params`` table in an object fragment
      -- is refused by ``jm apply`` with one sentence naming the row, the key
      and ``default = "<C constant>"``, and leaves the stub as it was; the
      working spelling applies and keeps the argument optional; every
      parameter table ``_keys`` derives as refusing the key is refused, a
      view's method params included (walked by nothing before); and an
      init-param's ``default_raw``, valid, still loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _keys

_CONST = "OFDM_CP_DEFAULT"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"{args}\n{r.stdout}\n{r.stderr}"


def _row(**over: str) -> "dict[str, str]":
    row = {"name": "cyclic_prefix", "type": "uint32_t"}
    row.update(over)
    return row


def test_doppler_shape_is_refused_by_apply(tmp_path):
    """The reported manifest, through the reported command."""
    _ok("new", "of", cwd=tmp_path)
    root = tmp_path / "of"
    _ok("object", "ofdm", cwd=root)
    _ok(
        "method", "ofdm", "add_run", "--param", "class_index:uint32_t",
        "--arg-type", "void", "--return-type", "int",
        cwd=root,
    )  # fmt: skip
    frag = root / "objects" / "ofdm.toml"
    text = frag.read_text(encoding="utf-8")
    head, sep, _rest = text.partition("[[ofdm.methods.params]]")
    assert sep, text
    inline = (
        "params = [\n"
        '    { name = "class_index", type = "uint32_t" },\n'
        '    { name = "cyclic_prefix", type = "uint32_t", '
        'default_raw = "%s" },\n]\n'
    )
    frag.write_text(head + inline % _CONST, encoding="utf-8")
    stub = root / "src" / "of" / "ofdm.pyi"
    before = stub.read_text(encoding="utf-8")

    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "ofdm.add_run(cyclic_prefix): `default_raw`" in out, out
    assert '`default = "<C constant>"`' in out, out
    assert stub.read_text(encoding="utf-8") == before

    # The spelling the refusal names applies, and the argument is optional.
    fixed = frag.read_text(encoding="utf-8").replace(
        f'default_raw = "{_CONST}"', f'default = "{_CONST}"'
    )
    frag.write_text(fixed, encoding="utf-8")
    _ok("apply", cwd=root)
    assert "cyclic_prefix: int = ...)" in stub.read_text(encoding="utf-8")


#: One manifest per refused parameter kind, the row placed where that
#: kind's table lives. Keyed by `_keys.KIND_KEYS` name, so a parameter
#: table the derivation gains fails `test_every_refused_kind_has_a_case`
#: until it is given one here.
_CASES = {
    "param": [
        lambda r: {"o": {"methods": [{"name": "m", "params": [r]}]}},
        lambda r: {"o": {"methods": [{"name": "m", "extra_args": [r]}]}},
        # gh-504's view: its method rows were walked by nothing.
        lambda r: {
            "o": {
                "views": [
                    {
                        "class_name": "V",
                        "methods": [{"name": "m", "params": [r]}],
                    }
                ]
            }
        },
    ],
    "function param": [
        lambda r: {
            "module": {"dsp": {"functions": [{"name": "f", "params": [r]}]}}
        },
    ],
    "kind create_arg": [
        lambda r: {"module": {"h": {"kind": "handle", "create_args": [r]}}},
    ],
    "kind method arg": [
        lambda r: {
            "module": {
                "h": {
                    "kind": "handle",
                    "methods": [{"name": "m", "fn": "f", "args": [r]}],
                }
            }
        },
    ],
    "kind init_param": [
        lambda r: {"module": {"c": {"kind": "capsule", "init_params": [r]}}},
        lambda r: {
            "module": {
                "h": {
                    "kind": "handle",
                    "factories": [
                        {"name": "f", "create_fn": "g", "init_params": [r]}
                    ],
                }
            }
        },
    ],
}


def test_every_refused_kind_has_a_case():
    assert {"param", "function param"} <= _keys.DEFAULT_RAW_REFUSED_KINDS
    assert set(_CASES) == _keys.DEFAULT_RAW_REFUSED_KINDS


@pytest.mark.parametrize(
    ("kind", "build"),
    [(k, b) for k, builds in _CASES.items() for b in builds],
    ids=[f"{k}-{i}" for k, bs in _CASES.items() for i in range(len(bs))],
)
def test_default_raw_on_a_parameter_is_refused(kind, build):
    cfg = build(_row(default_raw=_CONST))
    kinds = [u.kind for u in _keys.unknown_keys(cfg) if u.key == "default_raw"]
    assert kinds == [kind], kinds
    (err,) = _keys.default_raw_errors(cfg)
    assert "cyclic_prefix" in err and "`default_raw`" in err, err
    assert '`default = "<C constant>"`' in err, err
    assert _keys.default_raw_errors(build(_row(default=_CONST))) == []


@pytest.mark.parametrize(
    "cfg",
    [
        {"o": {"init_params": [_row(default_raw=_CONST)]}},
        {"o": {"views": [{"init_params": [_row(default_raw=_CONST)]}]}},
    ],
    ids=["object", "view"],
)
def test_an_init_params_default_raw_is_not_refused(cfg):
    """gh-1099's key, where it means something."""
    assert _keys.default_raw_errors(cfg) == []
