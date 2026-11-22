"""gh-2697 -- `optional = true` on a non-array init param is refused at load.

`optional` on an init param is array *dispatch* (gh-1004): the array's
presence selects `create_fn` instead of `<comp>_create`, and
`jm object --init-param` accepts the token only after an array type. A
manifest could still declare it on a scalar. No binding read it there, yet
the module `.pyi` rendered `window: int | None = None` and the docstring
`window : int or None` while `tp_init` parsed a plain integer seeded with
`0`: `Wlan(window=None)` raised `TypeError`, and `Wlan()` passed `0` to
`create()` -- a real value for doppler's window, silently.

`_context/_parse.init_param_optional_errors`, asked by `_config.load`,
refuses it on every non-array init param (scalar, enum, string enum, path,
bytes, capsule, `object` reference) of an object or a view, naming
`default` / `default_raw` instead. Every face reads the manifest through
`load`, so `apply`, `status` and `script` all refuse rather than render the
disagreeing stub. The types are read off jm's own registries, so a type
added later is asked without editing this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _types as T  # noqa: E402
from just_makeit._context._parse import (  # noqa: E402
    init_param_optional_errors,
)

#: Every type an init param can be declared with that is NOT an array:
#: each scalar jm knows, its pseudo-types, both enum spellings, a capsule
#: and an `object` reference (which carries no `type` at all).
_NON_ARRAY = (
    [{"type": t} for t in sorted(T.SUPPORTED_TYPES)]
    + [{"type": t} for t in sorted(T.PSEUDO_TYPES)]
    + [
        {"type": "enum:mode"},
        {"type": "string_enum:a,b"},
        {"type": "tt_state_t *", "capsule": "p.tt"},
        {"object": "tt"},
    ]
)

#: Every array type an init param accepts, which `optional` may dispatch.
_ARRAYS = sorted(f"{t}[]" for t in T.SUPPORTED_ARRAY_CTYPES)


def _row(**kw) -> dict:
    return {"name": "window", "optional": True, **kw}


def _ids(rows):
    return [r.get("type", r.get("object")) for r in rows]


@pytest.mark.parametrize("decl", _NON_ARRAY, ids=_ids(_NON_ARRAY))
def test_a_non_array_param_is_refused(decl):
    errs = init_param_optional_errors({"w": {"init_params": [_row(**decl)]}})
    assert len(errs) == 1, errs
    assert errs[0].startswith("[[w.init_params]] window: optional = true")
    assert "default_raw" in errs[0]


@pytest.mark.parametrize("decl", _NON_ARRAY, ids=_ids(_NON_ARRAY))
def test_a_view_param_is_refused(decl):
    cfg = {"w": {"views": [{"name": "v", "init_params": [_row(**decl)]}]}}
    errs = init_param_optional_errors(cfg)
    assert len(errs) == 1, errs
    assert errs[0].startswith("[[w.views.init_params]] window:")


@pytest.mark.parametrize("arr", _ARRAYS)
def test_an_array_may_still_dispatch(arr):
    row = _row(type=arr, create_fn="mk")
    assert init_param_optional_errors({"w": {"init_params": [row]}}) == []


def test_false_is_absence_and_a_string_true_is_asked():
    # The key is read through `_config._truthy` (gh-2705): the hand-written
    # `optional = "true"` (test_gh515 declares one on an array) is the
    # same declaration as `true`.
    row = {"name": "n", "type": "int", "optional": False}
    assert init_param_optional_errors({"w": {"init_params": [row]}}) == []
    row["optional"] = "true"
    assert len(init_param_optional_errors({"w": {"init_params": [row]}})) == 1
    row["type"] = "float[]"
    assert init_param_optional_errors({"w": {"init_params": [row]}}) == []


_WINDOW = (
    '\n[[wlan.init_params]]\nname = "window"\ntype = "uint32_t"\n'
    'optional = true\ndoc = "Window length."\n'
)


def _project(tmp_path: Path, module: bool) -> tuple[Path, Path]:
    """A project with object `wlan` (in module `dsp` when *module*), and the
    file its TOML lives in."""
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "p"
    if module:
        r = run_cli("module", "dsp", cwd=proj)
        assert r.returncode == 0, r.stderr
    args = ["object", "wlan", "--no-step"]
    if module:
        args += ["--module", "dsp"]
    r = run_cli(*args, cwd=proj)
    assert r.returncode == 0, r.stderr
    frag = proj / "objects" / "wlan.toml"
    toml = frag if frag.exists() else proj / "just-makeit.toml"
    return proj, toml


@pytest.mark.parametrize("module", [False, True], ids=["standalone", "module"])
@pytest.mark.parametrize("verb", ["apply", "status", "script"])
def test_every_face_refuses_the_issue_manifest(tmp_path, module, verb):
    proj, toml = _project(tmp_path, module)
    stubs = {p: p.read_text("utf-8") for p in proj.rglob("*.pyi")}
    with toml.open("a", encoding="utf-8") as f:
        f.write(_WINDOW)
    r = run_cli(verb, cwd=proj)
    assert r.returncode != 0, (r.stdout, r.stderr)
    assert "[[wlan.init_params]] window: optional = true" in r.stderr
    # The refusal comes before any write: no stub learned `| None = None`.
    assert {p: p.read_text("utf-8") for p in proj.rglob("*.pyi")} == stubs
