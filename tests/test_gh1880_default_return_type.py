"""gh-1880: an object's manifest records the step() return type it renders.

`jm object g --preset generator` (equally `--arg-type void`) scaffolded a
header whose `step()` returns `float _Complex` and a manifest that said
`return_type = "void"`. `jm status` was STALE on the fresh scaffold, and
`jm apply` re-rendered the binding from the manifest and broke the build
(`too few arguments to function 'r2_g_steps'`).

The default has one owner, `_context._sample.resolve_return_type`, which the
renderer calls. Three other places carried their own copy of the rule, each
without its `void -> float _Complex` branch, and now call it instead:

- `_config.add_component`, the one writer of an object's manifest entry --
  the bug as reported;
- `_config.return_type`, the reader, whose fallback for an absent key was a
  constant `float _Complex` whatever the `arg_type`;
- `_script`, deciding when `--return-type` is implicit. Fixing the writer
  alone would have broken this one: a deliberate `--arg-type void
  --return-type void` object would script WITHOUT its `--return-type` and
  replay as a generator.

The cases are derived where the source can supply them: every `--preset`
`jm object` offers, plus one `--arg-type` per branch of the resolver with no
`--return-type`, so the default is what is under test.

GATE: an object scaffolded with no --return-type records, in its manifest,
      the step() return type its header declares, so the fresh scaffold is
      not STALE; a manifest that omits return_type means what omitting
      --return-type means; and `jm script` replays every return type.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _jmrun import replay_script, run_cli

from just_makeit import _config as C
from just_makeit import _csym as CSYM
from just_makeit._cli_object import _PRESETS
from just_makeit._context import resolve_return_type

#: One `--arg-type` per branch of `resolve_return_type`.
_BRANCHES = {"array": "double[]", "void": "void", "scalar": "double"}

_CASES = {f"preset-{p}": ["--preset", p] for p in sorted(_PRESETS)}
_CASES.update({f"arg-{b}": ["--arg-type", t] for b, t in _BRANCHES.items()})


def _scaffold(tmp_path: Path, *flags: str) -> Path:
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    r = run_cli("object", "o", *flags, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _status_clean(root: Path) -> None:
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr


def _header_step_return(root: Path) -> "str | None":
    """The return type `o`'s header declares for step(); None if it has none.

    Read from the C, not the manifest: GNU style puts the storage class and
    return type on the line above the function name.
    """
    header = next((root / "native" / "inc").rglob("o_core.h"))
    text = header.read_text(encoding="utf-8")
    stem = re.escape(CSYM.stem(root, "o"))
    m = re.search(rf"^([^\n]*)\n{stem}_step\(", text, re.M)
    if m is None:
        return None
    decl = m.group(1).split()
    while decl and decl[0] in ("static", "inline", "JM_FORCEINLINE", "JM_HOT"):
        decl.pop(0)
    return " ".join(decl)


@pytest.mark.parametrize("flags", _CASES.values(), ids=_CASES.keys())
def test_a_fresh_scaffold_is_not_stale(flags, tmp_path):
    _status_clean(_scaffold(tmp_path, *flags))


@pytest.mark.parametrize("flags", _CASES.values(), ids=_CASES.keys())
def test_the_manifest_records_what_the_header_declares(flags, tmp_path):
    root = _scaffold(tmp_path, *flags)
    entry = C.load(root)["o"]
    declared = _header_step_return(root)
    if declared is None:
        # Only a step-less shape may have no step() to compare against:
        # `--no-step`, or array in AND array out, which has steps() alone
        # (`_context/_step.py`'s `_blockwise`). Anything else means the
        # header was not read, and must not pass as agreement.
        blockwise = all(
            entry[k].endswith("[]") for k in ("arg_type", "return_type")
        )
        assert entry["no_step"] == "true" or blockwise, entry
        return
    assert entry["return_type"] == declared, (
        f"objects/o.toml says return_type = {entry['return_type']!r} but "
        f"the header's step() returns {declared!r}; `jm apply` would "
        "render the binding against the manifest (gh-1880)"
    )


def test_the_generator_preset_strips_only_the_input_side(tmp_path):
    # `_PRESETS`'s own description: no input. A step() that also returned
    # nothing would be a tick, not a generator.
    root = _scaffold(tmp_path, *_CASES["preset-generator"])
    assert _header_step_return(root) not in (None, "void")


@pytest.mark.parametrize("arg_type", _BRANCHES.values(), ids=_BRANCHES.keys())
def test_an_absent_return_type_means_the_cli_default(arg_type, tmp_path):
    root = _scaffold(tmp_path, "--arg-type", arg_type)
    frag = root / "objects" / "o.toml"
    text = frag.read_text(encoding="utf-8")
    line = re.compile(r'^return_type = "[^"]*"\n', re.M)
    assert len(line.findall(text)) == 1, text
    frag.write_text(line.sub("", text), encoding="utf-8")
    assert "return_type" not in C.load(root)["o"]
    assert C.return_type(C.load(root), "o") == resolve_return_type(
        arg_type, None
    )
    _status_clean(root)


# Every return type against each kind of input, the default (None) among
# them: `jm script` must spell exactly the ones that are not implicit.
_SCRIPTED = [
    (at, rt)
    for at in ("void", "double")
    for rt in (None, "void", "double", "float _Complex")
] + [("double[]", None)]


@pytest.mark.parametrize(
    "arg_type,return_type",
    _SCRIPTED,
    ids=[f"{a}->{r}".replace(" ", "_") for a, r in _SCRIPTED],
)
def test_jm_script_replays_the_return_type(arg_type, return_type, tmp_path):
    (tmp_path / "a").mkdir()
    flags = ["--arg-type", arg_type]
    if return_type is not None:
        flags += ["--return-type", return_type]
    orig = _scaffold(tmp_path / "a", *flags)
    script = run_cli("script", cwd=orig)
    assert script.returncode == 0, script.stderr
    (tmp_path / "b").mkdir()
    replayed = replay_script(script.stdout, tmp_path / "b")
    want, got = C.load(orig)["o"], C.load(replayed)["o"]
    assert (got["arg_type"], got["return_type"]) == (
        want["arg_type"],
        want["return_type"],
    ), script.stdout
