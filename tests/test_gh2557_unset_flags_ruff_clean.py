"""gh-2557 / gh-2560: an app's omitted-when-unset call is ruff-stable.

A flag whose default Python cannot spell -- a ``default_raw`` init param, a
function parameter defaulted to a C constant -- is passed only when given,
through ``_app._py_call_args``. That block is laid out one argument per line
with a trailing comma in every bracket, so ruff keeps it at any width; the
names sit in a LIST because a one-element tuple's comma is syntax, not a
magic trailing comma, and ruff collapses it.

``test_gh1478_ruff_clean_scaffold.py`` cannot reach this: its shapes are CLI
calls, and no CLI call declares a ``default_raw`` or a function default (the
CLI fills a literal one). So the fixture is manifest fragments plus
``jm apply`` and ``jm app``, and ruff -- jm's pinned one, under the project's
own configuration and at jm's width -- must report nothing over it. One
omitted name and two, for the object and the function app, console and
PEP 723.

Needs the project environment (ruff), so it is in ``PROJECT_ENV_TESTS``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _downstream_gates as G
from _jmrun import run_cli
from just_makeit._docstring import STUB_TARGET_WIDTH

_OBJ = (
    '[g]\narg_type = "float"\nreturn_type = "float"\nno_state = "true"\n'
    '\n[[g.init_params]]\nname = "cap"\ntype = "double"\n'
    'default_raw = "NAN"\n'
    '\n[h]\narg_type = "float"\nreturn_type = "float"\nno_state = "true"\n'
    '\n[[h.init_params]]\nname = "lo"\ntype = "double"\n'
    'default_raw = "NAN"\n'
    '\n[[h.init_params]]\nname = "hi"\ntype = "double"\n'
    'default_raw = "NAN"\n'
)

_FN = (
    "[module.m]\nobjects = []\n"
    '\n[[module.m.functions]]\nname = "f"\nreturn_type = "double"\n'
    '\n[[module.m.functions.params]]\nname = "x"\ntype = "double"\n'
    'default = "INFINITY"\n'
)

#: Every `jm app` call, after the manifest is applied.
_APPS = [
    "app --target console --object g --name tool",
    "app --target pep723 --object h --name tool_h",
    "app --target console --module m --function f --name ftool",
    "app --target pep723 --module m --function f --name ftool_script",
]

_JM_WIDTH = ("--line-length", str(STUB_TARGET_WIDTH))


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("gh2557")
    assert run_cli("new", "p", cwd=tmp).returncode == 0
    root = tmp / "p"
    (root / "objects").mkdir(exist_ok=True)
    (root / "modules").mkdir(exist_ok=True)
    (root / "objects" / "g.toml").write_text(_OBJ, encoding="utf-8")
    (root / "modules" / "m.toml").write_text(_FN, encoding="utf-8")
    out = run_cli("apply", cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    for call in _APPS:
        out = run_cli(*call.split(), cwd=root)
        assert out.returncode == 0, call + "\n" + out.stdout + out.stderr
    return root


def _scripts(root: Path) -> list[Path]:
    """The app scripts, each proven to carry the omitted-when-unset form --
    a scan of files that lack it would pass for nothing."""
    found = [
        p
        for p in root.rglob("*.py")
        if "argparse" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == len(_APPS), found
    for p in found:
        assert "if getattr(args, k) is not None" in p.read_text(
            encoding="utf-8"
        ), p
    return found


@pytest.mark.parametrize("width", [(), _JM_WIDTH], ids=["config", "jm"])
def test_the_omitted_block_is_ruff_clean(
    project: Path, width: "tuple[str, ...]"
) -> None:
    _scripts(project)
    check = G._ruff(project, "check", *width, "--output-format=concise")
    assert not G._RUFF.findall(check), check
    reformat = G._REFORMAT.findall(
        G._ruff(project, "format", "--check", *width)
    )
    assert not reformat, G._ruff(project, "format", "--diff", *width)
