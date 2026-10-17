"""The C benchmark calls every method shape with its real arguments.

``bench_<comp>_core.c`` times each method by calling its kernel, and it
spelled that call per shape by hand, beside the prototype the sacred
``_core.h`` declares. They drifted: a ``single`` record method with a block
input is declared ``rec_t f(state, const float *in, size_t n_in)``, and
the benchmark called ``f(obj, 0.0f)`` -- "too few arguments", so the
generated tree did not build.

The call is now built from the prototype itself
(`_method._build_method_prototype`, the declaration the header carries):
each declared parameter gets the argument its role takes, and a kernel
taking a block input gets a buffer.

GATE: compiled, and registration-free over the method-shape roster of
`test_gh2259_batch_method_faces.SHAPES`: one object carrying a method of
every shape there, built with ``-Werror``, and the benchmark executable
must exist afterwards -- benchmarks build in the default target, so a
build that never compiled one would pass for nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _compilers import default_cc
from test_gh2259_batch_method_faces import SHAPES

from _jmrun import run_cli
from just_makeit import _incpath as INC
from just_makeit import _textio

#: The structs the record shapes name; the author declares them in the
#: sacred header. Read off the roster, so a new record shape there is not
#: missed: every ``--return-type`` that is no C type jm knows.
_RECORDS = sorted(
    {
        flags[flags.index("--return-type") + 1]
        for flags in SHAPES.values()
        if "--result-field" in flags
    }
)


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(
    not shutil.which("cmake") or default_cc() is None,
    reason="needs cmake and a C compiler",
)
def test_every_shape_benchmark_builds(tmp_path) -> None:
    assert _RECORDS, "the roster has no record shape to declare"
    _ok("new", "mp", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "mp"
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    for name, flags in SHAPES.items():
        _ok("method", "o", name, *flags, cwd=proj)
    header = INC.core_h(proj, "o")
    text = header.read_text(encoding="utf-8")
    include = f'#include "{INC.include("clib_common.h", proj)}"\n'
    assert text.count(include) == 1, text
    structs = "".join(
        f"typedef struct {{ double a; }} {r};\n" for r in _RECORDS
    )
    _textio.write_text(header, text.replace(include, include + structs))
    # The benchmark is create-only, written when the object was, before
    # any method existed: render it again from the full manifest.
    bench = proj / "native/benchmarks/bench_o_core.c"
    bench.unlink()
    _ok("apply", cwd=proj)
    src = bench.read_text(encoding="utf-8")
    assert "/* bench: single() */" in src, src
    for cmd in (
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "b",
            "-DBUILD_PYTHON=OFF",
            "-DCMAKE_C_FLAGS=-Wall -Werror",
        ],
        ["cmake", "--build", "b", "--target", "bench_o_core"],
    ):
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, (r.stdout + r.stderr)[-4000:])
    # Armed: the benchmark itself was compiled and linked.
    exe = [p for p in (proj / "b").rglob("bench_o_core*") if p.is_file()]
    assert any(p.suffix in ("", ".exe") for p in exe), exe
