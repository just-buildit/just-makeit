"""gh-2116 review: `jm add` names what was asked for, from the flag.

`jm add --param k:int:4` on an object in a `no_generate` module refused
with "cannot add state (k)": every message of the command called the row
"state", whichever flag gave it, while the help calls a ``--param`` a
constructor parameter.

GATE: for every flag in `_add.FLAG_NOUNS` -- the one spelling of the noun,
      so a flag added there is held here with no edit -- the refusal on a
      `no_generate` object, and the banner and prompt on a generated one
      (declined, so nothing is written), name that flag's noun and no
      other flag's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _add
from just_makeit import _config as C


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


@pytest.fixture(scope="module")
def shapes(tmp_path_factory) -> "dict[str, Path]":
    """``gen``: object ``o`` in a generated module; ``hand``: the same,
    with the module declared `no_generate`."""
    base = tmp_path_factory.mktemp("gh2116add")
    _ok(base, "new", "p")
    gen = base / "p"
    _ok(gen, "module", "mod")
    _ok(gen, "object", "o", "--module", "mod")
    hand = base / "hand"
    shutil.copytree(gen, hand, symlinks=True)
    cfg = C.load(hand)
    cfg["module"]["mod"]["no_generate"] = "true"
    cfg["module"]["mod"]["no_generate_reason"] = "hand-written by gh-2116"
    C.save(hand, cfg)
    return {"gen": gen, "hand": hand}


def _others(flag: str) -> "list[str]":
    """Every other flag's noun that is not part of this flag's."""
    mine = _add.FLAG_NOUNS[flag]
    return [n for f, n in _add.FLAG_NOUNS.items() if f != flag and n != mine]


@pytest.mark.parametrize("flag", sorted(_add.FLAG_NOUNS))
def test_the_refusal_names_the_flag(shapes, tmp_path, flag):
    root = tmp_path / "p"
    shutil.copytree(shapes["hand"], root, symlinks=True)
    r = run_cli("add", "--object", "o", flag, "k:int:4", "--force", cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1 and len(errors) == 1, r.stdout + r.stderr
    noun = _add.FLAG_NOUNS[flag]
    assert f"cannot add {noun} (k) to 'o'" in errors[0], errors[0]
    for other in _others(flag):
        assert f"add {other} (" not in errors[0], errors[0]


@pytest.mark.parametrize("flag", sorted(_add.FLAG_NOUNS))
def test_the_banner_and_prompt_name_the_flag(shapes, tmp_path, flag):
    root = tmp_path / "p"
    shutil.copytree(shapes["gen"], root, symlinks=True)
    r = run_cli("add", "--object", "o", flag, "k:int:4", cwd=root, stdin="n\n")
    assert r.returncode == 1, r.stdout + r.stderr
    noun = _add.FLAG_NOUNS[flag]
    out = r.stdout + r.stderr
    assert f"just-makeit: add {noun} (k) to 'o'" in out, out
    assert f"Add {noun} (k) to 'o'?" in out, out
    for other in _others(flag):
        assert f"dd {other} (" not in out, out
