"""gh-1478: a fresh scaffold's Python is clean under ruff's defaults.

A downstream that runs ruff (most do, doppler does) got a diff on its first
commit: ``E401``/``F401`` in the generated test, ``F841``/``E712`` in some
shapes, and ``ruff format`` rewriting ``__init__.py``, the ``.pyi``, the
benchmark and the test. gh-1432 was that class biting: the project's own
formatter fought jm's render and ``status --check`` then called jm's own file
STALE.

Gate A (gh-1443) holds every bundled EXAMPLE to this, per example, as a
ratchet. This holds the SHAPES a scaffold can take to it, with no ratchet at
all: each is scaffolded fresh through the CLI and ruff -- jm's pinned one,
run the way Gate A runs it, under the project's own configuration -- must
report nothing, over every file it finds. Registration-free over files: a
new generated file is checked the day it exists. A shape is added here when
an emitter branch is.

Needs the project environment (ruff), so it is in ``PROJECT_ENV_TESTS``.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

import _downstream_gates as G
from _jmrun import run_cli

#: Each shape: the CLI calls after ``jm new p``, one command per string. A
#: first call spelled ``new ...`` replaces the default ``new p``.
SHAPES: "dict[str, list[str]]" = {
    "scalar": ["object gain --arg-type float --return-type float"],
    "complex_default": ["object mix"],
    "no_step": ["object g --no-step --state k:int:3"],
    "no_state": ["object g --no-state --arg-type double --return-type double"],
    "no_state_no_step": ["object g --no-state --no-step"],
    "no_reset": ["object g --no-reset --no-step --state k:int:1"],
    "void_arg": ["object src --arg-type void --return-type float"],
    "array_arg": [
        "object a --arg-type float[] --return-type float"
        " --state coeffs:double[4]"
    ],
    "blockwise": ["object b --arg-type float[] --return-type float[]"],
    "variable_output": [
        "object d --arg-type float --return-type float --variable-output"
    ],
    "bool_state": [
        "object g --state on:bool:true --state x:double:1.5"
        " --arg-type float --return-type float"
    ],
    "init_param": [
        "object g --init-param n:size_t:16 --state k:double:1.0"
        " --arg-type float --return-type float"
    ],
    "header_only": [
        "object q --header-only --state scale:float:1.0"
        " --arg-type float --return-type int16_t"
    ],
    "methods": [
        "object g --arg-type float --return-type float",
        "method g scale --return-type double",
        "method g many --variable-output",
        "property g k2 --type int",
    ],
    "streamable": [
        "object g --streamable --arg-type float --return-type float",
        "object s --arg-type void --return-type float --async-stream",
    ],
    "serializable": [
        "object g --serializable --state k:int:1"
        " --arg-type float --return-type float"
    ],
    "perf": ["object g --arg-type float --return-type float", "perf"],
    "module": [
        "module m",
        "object fir --module m --arg-type float --return-type float",
        "object bq --module m --no-step --state k:int:1",
        "function clamp --module m",
        "view fir FirView --module m --create-fn fir_create_view"
        " --init-param scale:double:2.0",
    ],
    # gh-1522: --opaque-state requires --no-step and refuses --state (the
    # fields are hand-written), standalone and as a module object.
    "opaque_state": ["object g --opaque-state --no-step"],
    "opaque_state_module": [
        "module m",
        "object g --module m --opaque-state --no-step",
    ],
    "nested_module": ["module dsp.filters", "object fir --module dsp.filters"],
    "pytest_faces": [
        "new p --pytest --pytest-benchmark",
        "object g --state on:bool:true --arg-type float --return-type float",
        "object n --no-step --state k:int:1",
        "object b --arg-type float[] --return-type float[]",
        "module m",
        "object f --module m --arg-type void --return-type float",
    ],
    "app_console": [
        "object g --arg-type float --return-type float --state k:int:3",
        "app --target console --object g --name tool"
        " --flag 'label:const char *:fast:the label' --flag n:int:4",
    ],
    "app_pep723_blockwise": [
        "object b --arg-type float[] --return-type float[]",
        "app --target pep723 --object b --name bt --flag scale:double:1.0",
    ],
    # A complex generator gets the --sample_type/--file_type/--record axes.
    "app_sample_type": [
        "object gen --no-state --init-param freq:double:0.0 --arg-type void"
        " --return-type 'float _Complex' --mutable",
        "app --target console --object gen --name tool",
        "app --target pep723 --object gen --name tool",
    ],
    "app_console_generator": [
        "object s --arg-type void --return-type float",
        "app --target console --object s --name gen",
    ],
}


def _scaffold(tmp_path: Path, calls: "list[str]") -> Path:
    argvs = [shlex.split(c) for c in calls]
    if not argvs or argvs[0][0] != "new":
        argvs.insert(0, ["new", "p"])
    r = run_cli(*argvs[0], cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    for argv in argvs[1:]:
        r = run_cli(*argv, cwd=proj)
        assert r.returncode == 0, f"jm {' '.join(argv)}\n{r.stdout}{r.stderr}"
    return proj


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_fresh_scaffold_is_ruff_clean(tmp_path: Path, shape: str) -> None:
    proj = _scaffold(tmp_path, SHAPES[shape])
    py = sorted(proj.rglob("*.py")) + sorted(proj.rglob("*.pyi"))
    # A scan that finds nothing to scan proves nothing: every shape writes
    # at least a test, a benchmark and a stub.
    assert any(p.name.startswith("test_") for p in py), py
    assert any(p.suffix == ".pyi" for p in py), py
    check = G._ruff(proj, "check", "--output-format=concise")
    assert not G._RUFF.findall(check), (
        f"ruff check on a fresh {shape} scaffold:\n{check}"
    )
    reformat = G._REFORMAT.findall(G._ruff(proj, "format", "--check"))
    assert not reformat, (
        f"ruff format would rewrite a fresh {shape} scaffold:\n"
        + G._ruff(proj, "format", "--diff")
    )
