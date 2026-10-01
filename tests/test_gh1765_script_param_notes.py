"""gh-1765: `jm script` names every param key its command line cannot spell.

`_script` re-declared a param as ``--param name:type`` (``--init-param
name:type:...`` for a constructor) and dropped the rest of the row with no
word: `rank` and `elements_per_sample` (gh-805 §C), `doc`, `str_hint`. The
replayed project built without the rank guard or the interleave divisor and
the script said nothing. Only `enum` on a method param had a NOTE (gh-1021),
because it was the one key someone had listed; a method param's `default`,
which `jm method --param name:type=<default>` CAN spell, was dropped too.

The fix is the complement rather than a longer list: each param spelling
returns the keys it carries, and `_script._param_notes` names every other key
the row declares. These tests hold that from two sides:

- per face, for every key in that face's vocabulary in `_keys`, a
  representative value either changes the emitted command or is named by a
  NOTE -- exactly one of the two, so a NOTE never claims a key the command
  does carry. A key added to a vocabulary without a representative fails
  `test_every_accepted_key_has_a_representative`.
- end to end, a project built by the CLI is scripted and REPLAYED, and every
  key its param rows declare either arrives in the replayed manifest with the
  same value or is named by a NOTE. That oracle does not consult the carried
  sets at all, so it would catch one that claims a key it does not spell.

GATE: a param key `jm script` cannot replay is named in a ``# NOTE`` before
      the command, on method, view-method, module-function, object init and
      view init params.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import replay_script, run_cli
from just_makeit import _config as C
from just_makeit import _script as S
from just_makeit._keys import (
    FUNCTION_PARAM_KEYS,
    INIT_PARAM_KEYS,
    PARAM_KEYS,
)

#: A representative value for every key in every param vocabulary `_script`
#: replays. `name` and `type` are probed by CHANGING them from the base row;
#: every other key is added to it. Values are chosen so a key the CLI can
#: spell changes the command line: `required` / `optional` are True, a
#: default is non-empty.
_REPRESENTATIVE: dict[str, object] = {
    "name": "renamed",
    "type": "double",
    "default": "3",
    "enum": "mode_e",
    "out": True,
    "mutable": True,
    "doc": "The tap count.",
    "capsule": "p.handle",
    "header": "p/handle.h",
    "rank": 1,
    "elements_per_sample": 2,
    "role": "tag",
    "str_hint": "pass bytes",
    "default_raw": "P_DEFAULT",
    "real_type": "float64",
    "real_create_fn": "p_create_real",
    "optional": True,
    "create_fn": "p_create_opt",
    "required": True,
    "derived": "n",
    "c_type": "p_mode_t",
    "example_value": "4",
    "object": "other",
}

_BASE = {"name": "x", "type": "int"}


def _method_face(row: dict) -> tuple[str, str]:
    m = {"name": "g", "params": [row]}
    return "".join(S._method_flags(m, None)), "".join(S._method_notes(m))


def _function_face(row: dict) -> tuple[str, str]:
    fn = {"name": "f", "params": [row]}
    return "".join(S._function_flags(fn, "m")), "".join(S._function_notes(fn))


def _view_init_face(row: dict) -> tuple[str, str]:
    v = {"class_name": "V", "create_fn": "o_create_v", "init_params": [row]}
    return (
        "".join(S._view_flags(v, None)),
        "".join(S._init_param_notes(v["init_params"])),
    )


#: face -> (the vocabulary its rows are validated against, its emitter).
#: An object's init params go through the same `_init_param_carry` as a
#: view's; the end-to-end test below covers the object wiring in `run`.
_FACES = {
    "method param": (PARAM_KEYS, _method_face),
    "function param": (FUNCTION_PARAM_KEYS, _function_face),
    "init param": (INIT_PARAM_KEYS, _view_init_face),
}

_CASES = [
    pytest.param(face, key, id=f"{face.replace(' ', '_')}-{key}")
    for face, (vocab, _emit) in _FACES.items()
    for key in sorted(vocab)
]


def test_every_accepted_key_has_a_representative():
    vocab = PARAM_KEYS | FUNCTION_PARAM_KEYS | INIT_PARAM_KEYS
    missing = sorted(vocab - set(_REPRESENTATIVE))
    assert not missing, (
        f"param keys with no representative: {missing} -- add one to"
        " _REPRESENTATIVE so the gate covers it"
    )


@pytest.mark.parametrize("face,key", _CASES)
def test_each_key_is_carried_or_noted(face, key):
    """Exactly one of: the command line changes, or a NOTE names the key."""
    _vocab, emit = _FACES[face]
    base_cmd, base_notes = emit(dict(_BASE))
    assert not base_notes, base_notes  # the probe's base row is fully spelled
    row = {**_BASE, key: _REPRESENTATIVE[key]}
    cmd, notes = emit(row)
    carried = cmd != base_cmd
    noted = f" {key} = " in notes
    assert carried or noted, (
        f"{face} key {key!r} is neither spelled by the command nor named by"
        f" a NOTE:\n{cmd}"
    )
    assert not (carried and noted), (
        f"{face} key {key!r} is spelled by the command AND named as one it"
        f" cannot spell:\n{notes}{cmd}"
    )


def test_an_undeclared_value_is_not_noted():
    """`required = false` or `doc = ""` say nothing a replay could lose."""
    row = {**_BASE, "required": False, "doc": "", "rank": 0}
    _cmd, notes = _view_init_face(row)
    assert "required" not in notes and "doc" not in notes
    assert " rank = 0" in notes  # 0 is a value, not an absence


# --- end to end: build, script, replay --------------------------------------

#: Keys written into each param row after the CLI built it.
_ADDED = {
    "x": {"rank": 1, "elements_per_sample": 2, "doc": "Samples.",
          "str_hint": "pass bytes"},
    "y": {"rank": 1, "elements_per_sample": 2, "doc": "Samples.",
          "str_hint": "pass bytes"},
    "n": {"doc": "Tap count.", "example_value": "4"},
    "reps": {"doc": "Repeats."},
}  # fmt: skip


def _cli(cwd: Path, *args: str) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"{args}\n{r.stdout}{r.stderr}"


def _param_rows(cfg: dict) -> dict[str, dict]:
    """Every param row `_script` replays, by param name (unique here)."""
    rows: dict[str, dict] = {}
    for comp in C.components(cfg):
        for p in cfg[comp].get("init_params", []):
            rows[p["name"]] = p
        for m in C.methods(cfg, comp):
            for p in m.get("params", []):
                rows[p["name"]] = p
        for v in C.views(cfg, comp):
            for p in v.get("init_params", []):
                rows[p["name"]] = p
    for mod in C.modules(cfg):
        for fn in C.module_functions(cfg, mod):
            for p in fn.get("params", []):
                rows[p["name"]] = p
    return rows


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The issue's trigger, plus an object init param, a view init param and
    a defaulted method param."""
    _cli(tmp_path, "new", "jmp")
    root = tmp_path / "jmp"
    _cli(root, "module", "m")
    _cli(
        root,
        "function",
        "f",
        "--module",
        "m",
        "--param",
        "x:float[]",
        "--return-type",
        "int",
    )
    _cli(
        root,
        "object",
        "o",
        "--module",
        "m",
        "--no-step",
        "--arg-type",
        "void",
        "--return-type",
        "void",
        "--init-param",
        "n:int",
    )
    _cli(
        root,
        "method",
        "o",
        "g",
        "--module",
        "m",
        "--param",
        "y:float[]",
        "--param",
        "k:int=3",
        "--return-type",
        "int",
    )
    _cli(root, "view", "o", "Burst", "--module", "m",
         "--create-fn", "o_create_burst",
         "--init-param", "reps:int")  # fmt: skip
    cfg = C.load(root)
    rows = _param_rows(cfg)
    for pname, extra in _ADDED.items():
        rows[pname].update(extra)
    C.save(root, cfg)
    assert set(_ADDED) <= set(_param_rows(C.load(root)))
    return root


def test_the_trigger_names_every_dropped_key(project):
    text = run_cli("script", cwd=project).stdout
    for pname, extra in _ADDED.items():
        note = [ln for ln in text.splitlines() if f"'{pname}' declares" in ln]
        assert len(note) == 1, (pname, text)
        for key in extra:
            assert f" {key} = " in note[0], (pname, key, note[0])
    # the NOTE precedes the command it describes, never inside it
    assert text.index("'y' declares") < text.index("just-makeit method o g")
    assert text.index("'x' declares") < text.index("just-makeit function f")
    assert text.index("'n' declares") < text.index("just-makeit object o")
    assert text.index("'reps' declares") < text.index(
        "just-makeit view o Burst"
    )


def test_a_replay_keeps_or_names_every_key(project, tmp_path):
    """The independent oracle: replay the script and compare the manifests."""
    script = run_cli("script", cwd=project)
    assert script.returncode == 0, script.stderr
    (tmp_path / "replay").mkdir()
    replayed = replay_script(script.stdout, tmp_path / "replay")

    want = _param_rows(C.load(project))
    got = _param_rows(C.load(replayed))
    notes = [ln for ln in script.stdout.splitlines() if "# NOTE:" in ln]
    lost = []
    for pname, row in want.items():
        assert pname in got, f"param {pname!r} did not replay"
        named = " ".join(n for n in notes if f"'{pname}' declares" in n)
        for key, value in row.items():
            if not S._declares(value) or got[pname].get(key) == value:
                continue
            if f" {key} = " not in named:
                lost.append((pname, key, value, got[pname].get(key)))
    assert not lost, f"keys dropped by the replay with no NOTE: {lost}"
    # gh-1765: the method default is spelled, not merely noted.
    assert got["k"].get("default") == "3"
