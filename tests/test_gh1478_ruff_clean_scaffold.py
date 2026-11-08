"""gh-1478: a fresh scaffold's Python is clean under ruff's defaults.

And under isort (``I``) beside them (gh-2571): a downstream that selects it
-- doppler does -- found every scaffolded test, benchmark, stub and package
``__init__.py`` with its imports in one unsorted block.

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
from just_makeit._docstring import STUB_TARGET_WIDTH

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
        "app --target pep723 --object gen --name tool_script",
    ],
    # gh-2513: the functions contract test (gh-2127) at doppler's shapes: a
    # call too long for one line (two array params), a check_return status,
    # and skip reasons. Function names stay short: the package re-export is
    # single-line by contract (`_object._fmt_from_import`), which gh-2514
    # tracks; the long names are the parameters'.
    "functions_contract": [
        "module util",
        "function gauss --module util"
        " --param abscissae_out_buffer:double[]"
        " --param weights_out_buffer:double[] --return-type int"
        " --check-return",
        "function interp --module util"
        " --param fractional_sample_position:double"
        " --param number_of_neighbouring_taps:int --return-type double",
        "function describe --module util"
        " --param waveform_identifier_handle:int"
        " --return-type 'const char *'",
        "function fill --module util --out-type float64[n] --param n:int",
        # A complex scalar argument: `_types` `py_one` is the repr `(1+0j)`,
        # which a doctest expects; as source ruff format respaces it, so
        # `_fncontract._source` writes `1 + 0j` (doppler's square_clip on
        # 0.122.2).
        "function cclip --module util --param y:'double _Complex'"
        " --param lin:double --return-type double",
        # A parameter with no contract argument skips the test, and its
        # reason must fit too: a mid-length one goes alone inside the
        # parentheses, a long one wraps into concatenated pieces (doppler's
        # three E501s on 0.121.2).
        "function hexs --module util --param hex:'const char *'"
        " --return-type int",
        "function hexl --module util"
        " --param waveform_identifier_text_handle:'const char *'"
        " --return-type int",
    ],
    # Every wrapper skipped (an out_type shape, a parameter with no contract
    # argument): nothing calls the module, so the test must not import it,
    # or ruff's F401 fails and `--fix` fights the next `apply` (doppler's
    # ccsds, filter and wfm on 0.122.1).
    "functions_contract_all_skipped": [
        "module bits",
        "function asm --module bits --out-type float64[n] --param n:int",
        "function hexs --module bits --param hex:'const char *'"
        " --return-type int",
    ],
    # gh-2571: re-exports sorted naturally (`w2` before `w10`, `S2` before
    # `S10`), objects added in the order that sorts them wrong, a view in
    # the module, and both test faces' heads -- under ruff with `I`.
    "natural_order": [
        "module st",
        *(
            f"object {o} --module st --arg-type float --return-type float"
            for o in ("s10", "s2", "s1", "s11", "s3", "s4", "s5")
        ),
        "view s2 SV --module st --create-fn s2_create_view",
        "object w10 --arg-type float --return-type float",
        "object w2 --arg-type float --return-type float",
    ],
    "natural_order_pytest": [
        "new p --pytest --pytest-benchmark",
        "object w10 --arg-type float --return-type float",
        "object w2 --no-step --state k:int:1",
        "module m",
        "object s10 --module m --arg-type void --return-type float",
        "object s2 --module m --arg-type float[] --return-type float[]",
    ],
    "app_console_generator": [
        "object s --arg-type void --return-type float",
        "app --target console --object s --name gen",
    ],
}


#: ruff's defaults plus isort (gh-2571): selected on the command line
#: because the generated `pyproject.toml` configures no rules of its own.
_ISORT = ("--extend-select", "I")


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
    # gh-2571: and isort (`I`), which ruff's defaults leave off and a
    # downstream turns on -- doppler does. Every import block jm writes must
    # already be in isort order, or `ruff check --fix` rewrites jm's file
    # and `status --check` calls it STALE.
    check = G._ruff(proj, "check", *_ISORT, "--output-format=concise")
    assert not G._RUFF.findall(check), (
        f"ruff check on a fresh {shape} scaffold:\n{check}"
    )
    reformat = G._REFORMAT.findall(G._ruff(proj, "format", "--check"))
    assert not reformat, (
        f"ruff format would rewrite a fresh {shape} scaffold:\n"
        + G._ruff(proj, "format", "--diff")
    )


#: jm's own column target (`_docstring.STUB_TARGET_WIDTH`). A downstream
#: that sets ``line-length`` to it and selects E501 -- doppler does -- must
#: find jm's files clean too; ruff's defaults (88, E501 off) hid the
#: functions contract test's long calls from the check above (gh-2513).
_JM_WIDTH = ("--line-length", str(STUB_TARGET_WIDTH))


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_fresh_scaffold_is_clean_at_jms_width(
    tmp_path: Path, shape: str
) -> None:
    proj = _scaffold(tmp_path, SHAPES[shape])
    check = G._ruff(
        proj,
        "check",
        "--select",
        "E501",
        *_JM_WIDTH,
        "--output-format=concise",
    )
    assert not G._RUFF.findall(check), (
        f"E501 at {STUB_TARGET_WIDTH} on a fresh {shape} scaffold:\n{check}"
    )
    reformat = G._REFORMAT.findall(
        G._ruff(proj, "format", "--check", *_JM_WIDTH)
    )
    assert not reformat, (
        f"ruff format at {STUB_TARGET_WIDTH} would rewrite a fresh {shape}"
        " scaffold:\n" + G._ruff(proj, "format", "--diff", *_JM_WIDTH)
    )
