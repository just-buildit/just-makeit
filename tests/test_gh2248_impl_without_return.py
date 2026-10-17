"""gh-2248: an ``impl`` body with no ``return`` compiles in every shape.

A method's ``impl`` body lifted from a ``void`` reference function has no
``return``. `_method.run` completed it with ``return n;`` for a
``variable_output`` kernel, a name only the counted generator (void input,
no params) has: the input shape counts ``n_in``, an array param
``<p>_len``, and an all-scalar or ``no_count`` (gh-2233) kernel has no count
at all, so each of those failed to compile. Every other shape got nothing,
and fell off the end of a non-void function: a ``single`` record, a list of
records, a scalar, a borrow. So did the fixed stub itself for a method
returning a declared record element by value.

`_method.impl_default_return` is now the one answer, for every shape: the
count `_max_out_count_param` names (or ``<fn>_max_out(state)`` with none),
``0`` records, ``NULL`` for a borrow, nothing for ``void`` and a batch, and
otherwise the zero the stub returns -- ``(<T>){0}`` for a record.

GATE: compiled, with ``-Werror=return-type``. One method per shape, each
with an ``impl`` that returns nothing, in ``_core.c`` and header-only; the
C build and its test pass, and each appended return is the one written out
by hand in ``RETURNS``. The variable_output roster is held to the count
classifier: it must reach every count it names.
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
COUNTED = {
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

_FIELDS = [{"name": "a", "type": "float"}]

#: Every other shape a method's kernel can take.
OTHER = {
    "rec_one": {
        "arg_type": "void",
        "return_type": "rec_t",
        "single": True,
        "result_fields": _FIELDS,
    },
    "rec_list": {
        "arg_type": "float",
        "return_type": "rec_t",
        "result_fields": _FIELDS,
    },
    "fixed": {"arg_type": "float", "return_type": "float"},
    "fixed_status": {
        "arg_type": "float",
        "return_type": "int",
        "status_return": True,
    },
    "fixed_void": {"arg_type": "float", "return_type": "void"},
    "fixed_element": {"arg_type": "void", "return_type": "el_t"},
    "batch": {"arg_type": "float", "return_type": "float", "batch": True},
    "borrowed": {
        "arg_type": "void",
        "return_type": "float",
        "borrow": True,
        "borrow_count": "n",
        "params": [{"name": "n", "type": "size_t"}],
    },
}

#: A shape whose STUB returned nothing before any body was written: a fixed
#: method returning a declared record element by value. No ``impl``.
STUB_ONLY = {"element_stub": {"arg_type": "void", "return_type": "el_t"}}

#: What each kernel returns once the body is completed, written out by hand
#: rather than read from the code under test; None is no ``return`` at all.
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
    "rec_one": "(rec_t){0}",
    "rec_list": "0",
    "fixed": "(float)0.0f",
    "fixed_status": "(int)0",
    "fixed_void": None,
    "fixed_element": "(el_t){0}",
    "batch": None,
    "borrowed": "NULL",
    "element_stub": "(el_t){0}",
}

#: Writes one result and returns nothing, as a void reference function's
#: body would; the shapes with no ``out`` only touch the state.
IMPL_OUT = "(void)state;\nout[0] = 1.0f;"
IMPL = "(void)state;"

#: The structs the author declares in the sacred header: the result
#: record, and the declared record element (gh-1405).
TYPEDEFS = (
    "typedef struct { float a; } rec_t;\n"
    "typedef struct { int16_t i; int16_t q; } el_t;\n"
)


def _count(row: dict) -> "str | None":
    return _method._max_out_count_param(
        row["arg_type"], row.get("params"), bool(row.get("no_count"))
    )[1]


def test_the_roster_reaches_every_count_the_classifier_names() -> None:
    """Registration-free in the direction that matters: a count shape the
    classifier distinguishes and this roster lacks fails here, naming it.
    The classifier answers ``n_in``, ``n``, ``<p>_len`` or None, and None
    for two reasons (all-scalar params, ``no_count``)."""
    counts = {_count(r) for r in COUNTED.values()}
    assert {"n_in", "n", "v_len", None} <= counts, counts
    nones = {
        bool(r.get("no_count")) for r in COUNTED.values() if _count(r) is None
    }
    assert nones == {True, False}, nones


def _toml(header_only: bool) -> str:
    methods = (
        [
            {
                "name": name,
                "return_type": "float",
                "variable_output": True,
                **row,
                "impl": IMPL_OUT,
            }
            for name, row in COUNTED.items()
        ]
        + [
            {
                "name": name,
                **row,
                "impl": IMPL_OUT if row.get("batch") else IMPL,
            }
            for name, row in OTHER.items()
        ]
        + [{"name": name, **row} for name, row in STUB_ONLY.items()]
    )
    return C._dump(
        {
            "nr": {
                "arg_type": "float",
                "return_type": "float",
                "no_step": "true",
                **({"header_only": "true"} if header_only else {}),
                "records": [
                    {
                        "name": "el_t",
                        "fields": [
                            {"name": "i", "type": "int16_t"},
                            {"name": "q", "type": "int16_t"},
                        ],
                    }
                ],
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
    every kernel is ``static inline``."""
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    frag = tmp_path / "nr.toml"
    _textio.write_text(frag, _toml(header_only))
    r = run_cli("apply", str(frag), cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    header = root / "native/inc/p/nr/nr_core.h"
    text = header.read_text(encoding="utf-8")
    include = '#include "p/clib_common.h"\n'
    assert text.count(include) == 1, text
    _textio.write_text(header, text.replace(include, include + TYPEDEFS))
    where = header if header_only else root / "native/src/nr/nr_core.c"
    core = where.read_text(encoding="utf-8")
    assert set(RETURNS) == set(COUNTED) | set(OTHER) | set(STUB_ONLY)
    for name, ret in RETURNS.items():
        # Each kernel is defined there, ending the body that returned
        # nothing with the shape's own return.
        m = re.search(rf"\np_nr_{name}\([^;{{]*\)\n{{\n(.*?)\n}}", core, re.S)
        assert m, (name, core)
        last = m.group(1).rstrip().splitlines()[-1].strip()
        want = f"return {ret};" if ret else IMPL_OUT.splitlines()[-1]
        if ret is None and name != "batch":
            want = IMPL
        assert last == want, (name, m.group(1))
    for what, rc, out in _build(root):
        assert rc == 0, (what, out)
