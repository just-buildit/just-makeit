"""gh-2254: a recorded ``jm app`` run replays to the same values.

A ``jm app`` C executable's ``--record`` wrote each real flag with ``%g``
from ``_app._C_REC_FMT``, a per-type printf table kept beside the type
registry. ``%g`` keeps six significant digits, so ``--gain 0.123456789``
was recorded as ``0.123457`` and replaying the record ran with a different
value, silently. A function app's C face printed its real result through
a second such table (``_C_PRINTF``) with the same ``%g``, while its Python
face printed every digit.

Now one function, `_app.c_printf`, derives the conversion from the type's
``_CTYPE_META`` row: an integer through ``long long`` / ``unsigned long
long``, a real with the digits that read back to the same bits through
the ``strtof`` / ``strtod`` the flag parser (`_app.cli_number_c`) uses --
9 for ``float``, 17 for ``double``. A non-finite real is recorded as
``NaN`` / ``Infinity`` / ``-Infinity``, the spellings Python's ``json``
writes for the Python face's record and reads back, and ``strtod`` reads
too; ``%g``'s ``inf`` was not JSON at all.

The real types are every row an app parses whose kind is ``float``,
derived, so a real row added to the app's types is a case here. The
values need every digit (``0.1``, ``1/3``), sit at the edges (the type's
max, its smallest subnormal, ``-0``) or are not finite. numpy states each
expected bit pattern -- an oracle that is not the table under test.

GATE: for each real type an app parses, a recorded run replays to a
bit-identical value, and a function app's C face prints its real result
exactly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _app
from just_makeit import _types as T

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Every real type a `jm app` turns into a flag.
REALS = sorted(t for t in _app._C_PARSE if T._CTYPE_META[t]["kind"] == "float")


def _slug(ctype: str) -> str:
    return ctype.replace(" ", "_")


def _np(ctype: str):
    """numpy's scalar type for *ctype*'s row."""
    return getattr(np, T._CTYPE_META[ctype]["py_type"].split(".", 1)[1])


def _arguments(ctype: str) -> "list[str]":
    """Flag text whose value needs every digit, or sits at an edge."""
    fi = np.finfo(_np(ctype))
    return [
        "0.1",
        repr(1 / 3),
        repr(float(fi.max)),
        repr(-float(fi.max)),
        repr(float(fi.smallest_subnormal)),
        "-0",
        "inf",
        "-inf",
        "nan",
    ]


def _bits(ctype: str, value: float) -> bytes:
    """*value* as *ctype* holds it, as bytes; every NaN is one pattern."""
    held = _np(ctype)(value)
    return b"nan" if np.isnan(held) else held.tobytes()


def test_the_cases_are_not_vacuous():
    assert {"float", "double"} <= set(REALS)


def test_c_printf_is_derived_for_every_app_type():
    """Every type an app parses has an exact conversion: a real with the
    digits its width needs, an integer through a 64-bit conversion."""
    for t in _app._C_PARSE:
        fmt, cast = _app.c_printf(t)
        if t in REALS:
            bits = np.finfo(_np(t)).bits
            assert fmt == {32: "%.9g", 64: "%.17g"}[bits], t
        else:
            signed = np.iinfo(_np(t)).min < 0
            assert (fmt, cast) == (
                ("%lld", "(long long)")
                if signed
                else ("%llu", "(unsigned long long)")
            ), t


def _project(root: Path) -> Path:
    """An object app with a flag of every real type, and a function app
    returning each real type's argument."""
    assert run_cli("new", "rr", cwd=root).returncode == 0
    proj = root / "rr"
    r = run_cli(
        "object",
        "gen",
        "--preset",
        "generator",
        "--return-type",
        "float _Complex",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    flags = []
    for t in REALS:
        flags += ["--flag", f"x_{_slug(t)}:{t}:0"]
    r = run_cli(
        "app",
        "--target",
        "c",
        "--object",
        "gen",
        "--name",
        "tool",
        *flags,
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert run_cli("module", "m", cwd=proj).returncode == 0
    for t in REALS:
        fn = f"same_{_slug(t)}"
        r = run_cli(
            "function",
            fn,
            "--module",
            "m",
            "--param",
            f"x:{t}",
            "--return-type",
            t,
            cwd=proj,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        stub = proj / "native" / "src" / "m" / f"{fn}.c"
        text = stub.read_text(encoding="utf-8")
        body = text[text.index("{", text.index(f"rr_{fn}(")) :]
        stub.write_text(
            text.replace(body, "{\n    return x;\n}\n"), encoding="utf-8"
        )
        r = run_cli(
            "app",
            "--target",
            "c",
            "--function",
            fn,
            "--module",
            "m",
            "--name",
            f"p_{_slug(t)}",
            cwd=proj,
        )
        assert r.returncode == 0, r.stdout + r.stderr
    return proj


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


def _build(proj: Path, targets: "list[str]") -> Path:
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            "-DBUILD_PYTHON=OFF",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    for t in targets:
        _run(["cmake", "--build", "build", "--target", t], proj)
    return proj / "build"


def _exe(build: Path, name: str) -> Path:
    found = [p for p in build.rglob(name) if p.is_file()]
    assert found, f"{name} was not built"
    return found[0]


def _record(tool: Path, flag: str, arg: str, rec: Path) -> str:
    """Run *tool* with ``--<flag> <arg>``; the recorded text of *flag*."""
    r = subprocess.run(
        [
            str(tool),
            f"--{flag}",
            arg,
            "--count",
            "0",
            "--record",
            str(rec),
            "--output",
            str(rec.with_suffix(".bin")),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (flag, arg, r.stderr)
    # The raw token: what a replay passes back on the command line. The
    # record must be JSON that Python's reader takes, non-finite included.
    raw = json.loads(
        rec.read_text(encoding="utf-8"),
        parse_float=str,
        parse_int=str,
        parse_constant=str,
    )
    return raw[flag]


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_recorded_run_replays_bit_identical(tmp_path):
    proj = _project(tmp_path)
    fns = [f"p_{_slug(t)}" for t in REALS]
    build = _build(proj, ["tool", *fns])
    tool = _exe(build, "tool")
    rec = tmp_path / "record.json"
    problems = []
    for ctype in REALS:
        flag = f"x_{_slug(ctype)}"
        printer = _exe(build, f"p_{_slug(ctype)}")
        for arg in _arguments(ctype):
            want = _bits(ctype, float(arg))
            what = f"--{flag} {arg} ({ctype})"
            first = _record(tool, flag, arg, rec)
            if _bits(ctype, float(first)) != want:
                problems.append(f"{what}: recorded as {first}")
                continue
            again = _record(tool, flag, first, rec)
            if again != first:
                problems.append(f"{what}: {first} replayed as {again}")
            r = subprocess.run(
                [str(printer), "--x", arg], capture_output=True, text=True
            )
            out = r.stdout.strip()
            if r.returncode != 0 or _bits(ctype, float(out)) != want:
                problems.append(f"{printer.name} {arg}: printed {out!r}")
    assert not problems, "\n".join(problems)
