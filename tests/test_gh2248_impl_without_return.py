"""gh-2248: an ``impl`` body with no ``return`` compiles in every shape.

A ``variable_output`` kernel returns how many results it wrote. An
``impl`` body lifted from a ``void`` reference function has no ``return``,
so `_method.run` appends one -- and appended ``return n;`` whatever the
kernel's parameters were. Only the counted generator (void input, no
params) has an ``n``: the input shape counts ``n_in``, an array param
``<p>_len``, and an all-scalar or ``no_count`` (gh-2233) kernel has no
count at all, so each of those `_core.c` failed to compile.

The appended return now names the count `_method._max_out_count_param`
reads off the shape -- the one ``<fn>_max_out()`` takes, mirroring the
kernel's own -- and a kernel with none returns its ``<fn>_max_out(state)``,
the bound the binding sized ``out`` with: the body filled it.

GATE: compiled. One method per variable_output count shape, each with an
``impl`` that returns nothing, and the C build and its test pass. The
roster is held to the classifier: it must reach every count it names.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _method
from just_makeit import _textio

#: Every way a variable_output method's kernel counts, keyed by the method
#: name. ``max_out`` is set where the shape's bound must not be a
#: placeholder (gh-2233 refuses one there).
SHAPES = {
    "arg": {"arg_type": "float"},
    "arg_capacity": {
        "arg_type": "float",
        "pass_capacity": True,
        "max_out": 4,
    },
    "arg_params": {
        "arg_type": "float",
        "params": [{"name": "g", "type": "double"}],
    },
    "array_param": {
        "arg_type": "void",
        "params": [{"name": "v", "type": "float[]"}],
    },
    "generator": {"arg_type": "void"},
    "generator_multi": {"arg_type": "void", "multi_output": ["double"]},
    "scalar_params": {
        "arg_type": "void",
        "params": [{"name": "g", "type": "double"}],
        "max_out": 4,
    },
    "no_count": {"arg_type": "void", "no_count": True, "max_out": 4},
    "no_count_int": {
        "arg_type": "void",
        "no_count": True,
        "max_out": 4,
        "count_type": "int",
        "error_negative": True,
    },
}

#: What each kernel returns once the body is completed, written out by hand
#: rather than read from the classifier: the count it was handed, or with
#: none, the bound the binding sized ``out`` with.
RETURNS = {
    "arg": "n_in",
    "arg_capacity": "n_in",
    "arg_params": "n_in",
    "array_param": "v_len",
    "generator": "n",
    "generator_multi": "n",
    "scalar_params": "p_nr_scalar_params_max_out(state)",
    "no_count": "p_nr_no_count_max_out(state)",
    "no_count_int": "(int)p_nr_no_count_int_max_out(state)",
}

#: Writes one result and returns nothing, as a void reference function's
#: body would.
IMPL = "(void)state;\nout[0] = 1.0f;"


def _count(row: dict) -> "str | None":
    return _method._max_out_count_param(
        row["arg_type"], row.get("params"), bool(row.get("no_count"))
    )[1]


def test_the_roster_reaches_every_count_the_classifier_names() -> None:
    """Registration-free in the direction that matters: a count shape the
    classifier distinguishes and this roster lacks fails here, naming it.
    The classifier answers ``n_in``, ``n``, ``<p>_len`` or None, and None
    for two reasons (all-scalar params, ``no_count``)."""
    counts = {_count(r) for r in SHAPES.values()}
    assert {"n_in", "n", "v_len", None} <= counts, counts
    nones = {
        bool(r.get("no_count")) for r in SHAPES.values() if _count(r) is None
    }
    assert nones == {True, False}, nones


def _toml(rows: dict, header_only: bool) -> str:
    methods = [
        {
            "name": name,
            "return_type": "float",
            "variable_output": True,
            **row,
            "impl": IMPL,
        }
        for name, row in rows.items()
    ]
    return C._dump(
        {
            "nr": {
                "arg_type": "float",
                "return_type": "float",
                "no_step": "true",
                **({"header_only": "true"} if header_only else {}),
                "methods": methods,
            }
        }
    )


def _build(root: Path) -> list:
    steps = []
    for cmd in (
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "b",
            "-DBUILD_PYTHON=OFF",
            "-DCMAKE_C_FLAGS=-Werror=return-type",
        ],
        ["cmake", "--build", "b"],
        ["ctest", "--test-dir", "b", "--output-on-failure"],
    ):
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        steps.append((cmd[:2], r.returncode, (r.stdout + r.stderr)[-3000:]))
        if r.returncode:
            break
    return steps


@pytest.mark.skipif(
    not shutil.which("cmake") or default_cc() is None,
    reason="needs cmake and a C compiler",
)
@pytest.mark.parametrize(
    "header_only", [False, True], ids=["core_c", "header_only"]
)
def test_an_impl_without_a_return_builds_in_every_shape(
    tmp_path, header_only
) -> None:
    """In ``_core.c``, and in the header of a header-only component, where
    the kernel and its ``_max_out`` are both ``static inline``."""
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    frag = tmp_path / "nr.toml"
    _textio.write_text(frag, _toml(SHAPES, header_only))
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    where = (
        root / "native/inc/p/nr/nr_core.h"
        if header_only
        else root / "native/src/nr/nr_core.c"
    )
    core = where.read_text(encoding="utf-8")
    for name in SHAPES:
        # Each kernel is defined there, with the body that returns nothing.
        assert re.search(rf"\np_nr_{name}\([^;{{]*\)\n{{", core), core
    assert core.count("out[0] = 1.0f;") == len(SHAPES), core
    assert set(RETURNS) == set(SHAPES)
    for name, ret in RETURNS.items():
        body = core[core.index(f"\np_nr_{name}(") :]
        body = body[: body.index("\n}")]
        assert body.rstrip().endswith(f"return {ret};"), (name, body)
    for what, rc, out in _build(root):
        assert rc == 0, (what, out)
