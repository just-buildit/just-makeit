"""gh-2254: an app's csv output reads back to the samples it was given.

A ``jm app`` over a complex-float stream offers ``--file_type csv``: one
``I,Q`` line per sample, written by the C face's ``jm_write_block`` (which a
composer's c-face CLI shares) and by the Python faces' ``_PY_PACK_WRITE``.
A ``cf32`` line was ``%0.9f`` on both: nine places after the point, so an
ordinary small amplitude -- ``1e-12``, any subnormal -- was written as
``0.000000000``, and the data was gone. ``cf64`` already wrote ``%0.17g``.

Now both faces spell each component with the conversion `_app.c_printf`
gives its type (``%.9g`` float, ``%.17g`` double), the digits that read
back to the same bits. Python's ``%`` and C's ``printf`` both round
correctly, so the two faces write the same bytes.

The stream is a table of float32 samples the kernel emits: values that
need every digit, sit at the edges of the type (max, the smallest and
largest subnormal, ``-0``) or are far below the old nine places. numpy
states each expected bit pattern -- an oracle that is not the code under
test.

GATE: for ``cf32`` and ``cf64``, the C and Python faces write byte-identical
csv, and every line reads back to the sample's exact bits.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc
from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

_F32 = np.finfo(np.float32)

#: The I and Q of each sample the kernel emits, as float32.
SAMPLES = [
    (0.1, 1 / 3),
    (1e-12, -1e-12),
    (float(_F32.max), -float(_F32.max)),
    (float(_F32.smallest_subnormal), -float(_F32.smallest_subnormal)),
    (float(_F32.smallest_normal) - float(_F32.smallest_subnormal), -0.0),
    (123456.789, 5e-10),
]


def _f32(v: float) -> float:
    return float(np.float32(v))


def _c_literal(v: float) -> str:
    """*v* as float32, spelled exactly: a C99 hex-float literal."""
    return float.hex(_f32(v)) + "f"


def _project(root: Path) -> Path:
    assert run_cli("new", "cv", cwd=root).returncode == 0
    proj = root / "cv"
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
    for args in (
        ("--target", "c", "--name", "tool"),
        ("--target", "pep723", "--name", "pytool"),
    ):
        r = run_cli("app", "--object", "gen", *args, cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr
    # The kernel emits the table, from the start of each block.
    core = proj / "native" / "src" / "gen" / "gen_core.c"
    text = core.read_text(encoding="utf-8")
    call = "output[i] = cv_gen_step(state);"
    assert text.count(call) == 1, text
    table = ", ".join(
        f"{_c_literal(re)} + {_c_literal(im)} * I" for re, im in SAMPLES
    )
    text = text.replace(
        call,
        "{\n"
        f"            static const float _Complex tab[] = {{{table}}};\n"
        "            output[i] = tab[i % (sizeof tab / sizeof tab[0])];\n"
        "        }",
    )
    core.write_text(text, encoding="utf-8")
    return proj


def _run(cmd: list, cwd: Path, env: "dict | None" = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


def _exe(build: Path, name: str) -> Path:
    found = [p for p in build.rglob(name) if p.is_file()]
    assert found, f"{name} was not built"
    return found[0]


def _bits(v: float) -> bytes:
    return np.float32(v).tobytes()


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_csv_reads_back_exactly_on_both_faces(tmp_path):
    proj = _project(tmp_path)
    build = proj / "build"
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        proj,
    )
    _run(["cmake", "--build", "build"], proj)
    tool = _exe(build, "tool")
    env = dict(os.environ, PYTHONPATH=str(proj / "src"))
    n = str(len(SAMPLES))
    problems = []
    for st in ("cf32", "cf64"):
        flags = ["--count", n, "--sample_type", st, "--file_type", "csv"]
        c_out, py_out = tmp_path / f"c-{st}.csv", tmp_path / f"py-{st}.csv"
        _run([str(tool), *flags, "--output", str(c_out)], proj)
        _run(
            [sys.executable, "pytool.py", *flags, "--output", str(py_out)],
            proj,
            env,
        )
        c_text = c_out.read_text(encoding="utf-8")
        if py_out.read_text(encoding="utf-8") != c_text:
            problems.append(
                f"{st}: the faces differ\nC:\n{c_text}\nPython:\n"
                + py_out.read_text(encoding="utf-8")
            )
        lines = c_text.splitlines()
        if len(lines) != len(SAMPLES):
            problems.append(f"{st}: {len(lines)} lines:\n{c_text}")
            continue
        for line, (re, im) in zip(lines, SAMPLES):
            got = [float(x) for x in line.split(",")]
            if [_bits(g) for g in got] != [_bits(re), _bits(im)]:
                problems.append(f"{st}: {line!r} is not ({re!r}, {im!r})")
            if st == "cf64" and got != [_f32(re), _f32(im)]:
                problems.append(f"{st}: {line!r} is not the float exactly")
    assert not problems, "\n".join(problems)
