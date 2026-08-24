"""gh-1148: every generated header compiles as C++11.

jm has always emitted `extern "C"` guards — in `component_core.h`,
`module_core.h` and `umbrella.h`. For the default component shape they did not
work, and the guard was inert:

    native/inc/eng/eng_core.h:61:1: error: expected initializer before 'eng_step'
       61 | eng_step(const eng_state_t *state, float complex x)

`clib_common.h` includes `<complex.h>`. In C99 that defines `complex` as a
macro for `_Complex`; in C++ the same include maps to `<complex>`, where
`complex` is `std::complex` and the macro does not exist. So `float complex`
in a prototype is a syntax error, and every complex-typed component's header
was uncompilable from C++ — which is most of them, `float _Complex` being the
default `arg_type`. A component with no complex in its surface compiled fine,
which is why nothing noticed.

The fix restores C99's spelling under `__cplusplus`. What that buys is the
mixed-language case and nothing more: an author with C99 *and* C++11
algorithms can include a jm component's header from their own C++ and call it.
jm generates nothing new and learns no second language — implementing a
component *in* C++ is gh-1149, and tabled.

The type crosses the boundary; C99's complex *arithmetic* vocabulary does not.
`I`, `_Complex_I`, `creal()`, `cimag()` are C-only, and a C++ caller uses GNU
`__real__` / `__imag__`. That is a fact about the two languages — see the
comment in the template for why jm must not define `I` for C++.

Both halves are tested, because compiling is not calling: the sweep proves
every header parses, and the linked test proves a value actually survives the
crossing. A header that compiles while the ABI disagrees would pass the first
and fail the second.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
_needs_cxx = pytest.mark.skipif(_CXX is None, reason="no C++ compiler on PATH")


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """One component of each surface shape, plus a module and its umbrella.

    The shapes matter and are not decoration: `real` is the shape that
    compiled as C++ all along, so a fixture with only complex components
    could not tell "the fix works" from "the sweep is running on nothing",
    and one with only real components would have reported the bug fixed
    before it was written.
    """
    tmp = tmp_path_factory.mktemp("cxx")
    assert _cli("new", "yy", cwd=tmp).returncode == 0
    root = tmp / "yy"
    assert (
        _cli(
            "object", "cplx", "--state", "gain:double:1.0", cwd=root
        ).returncode
        == 0
    )
    assert (
        _cli(
            "object",
            "real",
            "--arg-type",
            "double",
            "--return-type",
            "double",
            "--state",
            "g:double:1.0",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli("object", "arr", "--state", "taps:float[8]", cwd=root).returncode
        == 0
    )
    assert _cli("module", "dsp", cwd=root).returncode == 0
    assert _cli("object", "filt", "--module", "dsp", cwd=root).returncode == 0
    return root


@_needs_cxx
class TestEveryGeneratedHeaderIsCxxIncludable:
    def test_sweep(self, project: Path, tmp_path: Path) -> None:
        """Derived from the tree, with no exemption list.

        Every header jm puts under `native/inc` is one it invites a C++ TU to
        include, because every one of them carries or includes an `extern "C"`
        block. A new generated header is covered on the day it is generated.
        """
        inc = project / "native" / "inc"
        headers = sorted(
            p.relative_to(inc).as_posix() for p in inc.rglob("*.h")
        )
        assert len(headers) >= 6, headers
        import sysconfig

        failures = []
        for rel in headers:
            src = tmp_path / "probe.cpp"
            src.write_text(f'#include "{rel}"\nint main() {{ return 0; }}\n')
            r = subprocess.run(
                [
                    _CXX,
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-c",
                    str(src),
                    f"-I{inc}",
                    f"-I{sysconfig.get_paths()['include']}",
                    "-o",
                    str(tmp_path / "probe.o"),
                ],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                # The WHOLE message. Truncating it to the first two
                # lines cost a CI round trip: both were "In file
                # included from ...", which named the symptom and
                # nothing about the cause.
                failures.append(f"--- {rel}\n{r.stderr.strip()}")
        assert not failures, "\n".join(failures)


@pytest.mark.skipif(
    _CC is None or _CXX is None, reason="needs both a C and a C++ compiler"
)
class TestTheAbiSurvivesTheCrossing:
    """Compiling is not calling.

    This links a C++11 translation unit — holding a `std::vector` of the
    component's own complex sample type — against the core compiled by a C99
    compiler, and runs it. A header that parsed while the ABI disagreed would
    pass the sweep above and fail here.
    """

    def test_a_cxx11_tu_calls_the_c99_core(
        self, project: Path, tmp_path: Path
    ) -> None:
        inc = project / "native" / "inc"
        core_c = project / "native" / "src" / "cplx" / "cplx_core.c"
        caller = tmp_path / "caller.cpp"
        caller.write_text(
            '#include "cplx/cplx_core.h"\n'
            "#include <vector>\n"
            "#include <cstdio>\n"
            "int main() {\n"
            "    std::vector<float _Complex> xs;\n"
            "    float _Complex z;\n"
            "    __real__ z = 3.0f;\n"
            "    __imag__ z = 4.0f;\n"
            "    xs.push_back(z);\n"
            "    cplx_state_t *o = cplx_create(2.0);\n"
            "    if (!o) return 1;\n"
            "    float _Complex y = cplx_step(o, xs.at(0));\n"
            '    std::printf("%.1f %.1f\\n", (double)__real__ y,\n'
            "                (double)__imag__ y);\n"
            "    cplx_destroy(o);\n"
            "    return 0;\n"
            "}\n"
        )
        core_o = tmp_path / "core.o"
        assert (
            subprocess.run(
                [
                    _CC,
                    "-std=c99",
                    "-c",
                    str(core_c),
                    f"-I{inc}",
                    "-o",
                    str(core_o),
                ],
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        caller_o = tmp_path / "caller.o"
        r = subprocess.run(
            [
                _CXX,
                "-std=c++11",
                "-Wall",
                "-c",
                str(caller),
                f"-I{inc}",
                "-o",
                str(caller_o),
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        exe = tmp_path / "mixed"
        # The C++ driver, deliberately: linking C++ objects with the C driver
        # leaves the runtime undefined. That is gh-1149's problem, not this
        # one — here the C++ side is the CALLER and jm's core stays C.
        assert (
            subprocess.run(
                [_CXX, str(caller_o), str(core_o), "-o", str(exe), "-lm"],
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "3.0 4.0", out.stdout


class TestTheUmbrellaDoesNotWrapItsIncludes:
    """No compiler needed, which is the point.

    The sweep above skips wherever a C++ compiler is absent, and this defect
    hid on Linux for as long as the umbrella has existed: it opened
    `extern "C" {` and the component `#include`s landed inside it, dragging
    `<complex.h>`, `<stdlib.h>` and `<string.h>` in with them. Including a C++
    standard header inside `extern "C"` is ill-formed; libstdc++ tolerates it,
    libc++ does not. So it compiled on every machine I had and failed on
    macOS CI.

    A structural assertion runs everywhere and does not care which standard
    library is installed. The compiled sweep is the one that proves the whole
    thing works; this is the one that will still be running when someone
    reintroduces the wrapper.

    The umbrella declares no function of its own, so it needs no guard at all
    — every header it includes carries one.
    """

    def test_the_generated_umbrella_opens_no_extern_c_block(
        self, project: Path
    ) -> None:
        umbrella = (project / "native" / "inc" / "yy.h").read_text("utf-8")
        assert '#include "cplx/cplx_core.h"' in umbrella, umbrella
        assert 'extern "C" {' not in umbrella, umbrella

    def test_the_component_headers_still_carry_theirs(
        self, project: Path
    ) -> None:
        """The converse, so "delete the guards" cannot pass this file: the
        per-component headers are where `extern "C"` belongs, and they open
        it AFTER including `clib_common.h`, not around it."""
        h = (project / "native" / "inc" / "cplx" / "cplx_core.h").read_text(
            "utf-8"
        )
        assert 'extern "C" {' in h
        assert h.index('#include "clib_common.h"') < h.index('extern "C" {')
