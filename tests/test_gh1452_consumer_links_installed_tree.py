"""gh-1452: a consumer of an installed jm project must be able to link it.

jm's headers DEFINE: `step()` is `static inline` by default and
`JM_FORCEINLINE` under --perf, and `jm_simd.h`/`jm_perf.h` are header-only.
So a consumer that calls `step()` compiles the body into its OWN object,
and anything that body calls -- `powf`, `sqrtf`, `fminf` -- is an undefined
symbol in the consumer. `ld` has not resolved a consumer's undefined symbol
through a dependency's NEEDED since binutils 2.22
(`--no-copy-dt-needed-entries`), so libm is part of the library's LINK
INTERFACE.

The generated `.pc` said `Libs: -L${libdir} -l<pkg>` and the exported
targets carried no INTERFACE_LINK_LIBRARIES, so the consumer failed:

    c.c: undefined reference to `powf'

doppler found it on its PUBLISHED packages in clean containers, through
pkg-config and `find_package` both -- because every smoke it had compiled a
consumer that happened not to reference libm.

This is that consumer. It builds against the INSTALLED tree using only what
`pkg-config` and `find_package` report, which is the gh-1443 thesis in one
test: exercise the generated project the way a downstream does.

Lives on PROJECT_ENV_TESTS: it needs cmake, a C compiler and pkg-config.

GATE: a program linked only against what an installed jm project advertises
      builds, shared and static, through pkg-config and find_package.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402

import os
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli

_CONSUMER = (
    '#include "<<P>>gain/gain_core.h"\n'
    "int main(void) {\n"
    "    gain_state_t *g = gain_create(1.0f);\n"
    "    float y = gain_step(g, 4.0f);\n"
    "    gain_destroy(g);\n"
    "    return y > 0 ? 0 : 1;\n"
    "}\n"
)


def _run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return r


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """Scaffold, give `step()` a libm dependency, build and install."""
    root = tmp_path_factory.mktemp("w")
    assert (
        run_cli(
            "new",
            "demo",
            "jmpc",
            "--object",
            "gain",
            "--state",
            "level:float:1.0",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=root,
        ).returncode
        == 0
    )
    proj = root / "jmpc"
    h = proj / INC_ROOT / "gain" / "gain_core.h"
    s = h.read_text()
    s = s.replace(
        "#ifndef GAIN_CORE_H", "#include <math.h>\n#ifndef GAIN_CORE_H", 1
    )
    # Anchored on the DEFINITION: the header's doc comment carries an
    # example call `gain_step(obj, 0.0f)` first, and a bare `gain_step(`
    # match edits that instead, producing an unbuildable header.
    i = s.index("gain_step(const gain_state_t *state")
    o = s.index("{", i)
    c = s.index("\n}", o)
    h.write_text(
        s[:o]
        + "{\n    return sqrtf(fabsf(x)) * powf(10.0f, state->level / 20.0f);"
        + s[c:]
    )
    pfx = proj / "pfx"
    for cmd in (
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "b",
            "-DBUILD_PYTHON=OFF",
            f"-DCMAKE_INSTALL_PREFIX={pfx}",
        ],
        ["cmake", "--build", "b"],
        ["cmake", "--install", "b"],
    ):
        r = _run(cmd, proj)
        assert r.returncode == 0, (cmd, r.stdout[-1500:], r.stderr[-1500:])
    # gh-1583: an installed package's headers are included as `<pkg>/...`.
    (proj / "c.c").write_text(_CONSUMER.replace("<<P>>", INC.prefix(proj)))
    return proj, pfx


def _pc_env(pfx: Path) -> dict:
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(pfx / "lib" / "pkgconfig")
    return env


def _pkg(pfx: Path, *args: str) -> "list[str]":
    r = _run(["pkg-config", *args, "demo"], pfx, _pc_env(pfx))
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


class TestPkgConfigConsumer:
    def test_libs_carries_libm(self, installed):
        _, pfx = installed
        assert "-lm" in _pkg(pfx, "--libs"), _pkg(pfx, "--libs")

    def test_a_shared_consumer_links(self, installed):
        proj, pfx = installed
        r = _run(
            [
                "cc",
                "-O2",
                "c.c",
                *_pkg(pfx, "--cflags", "--libs"),
                "-o",
                "c_shared",
            ],
            proj,
        )
        assert r.returncode == 0, r.stderr

    def test_a_static_consumer_links(self, installed):
        proj, pfx = installed
        r = _run(
            [
                "cc",
                "-O2",
                "c.c",
                *_pkg(pfx, "--cflags", "--libs", "--static"),
                "-o",
                "c_static",
            ],
            proj,
        )
        assert r.returncode == 0, r.stderr


class TestFindPackageConsumer:
    def test_shared_and_static_targets_link(self, installed):
        proj, pfx = installed
        cons = proj / "cons"
        cons.mkdir(exist_ok=True)
        (cons / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(cons C)\n"
            "find_package(demo REQUIRED)\n"
            "add_executable(c ../c.c)\n"
            "target_link_libraries(c PRIVATE demo::demo)\n"
            "add_executable(cs ../c.c)\n"
            "target_link_libraries(cs PRIVATE demo::demo-static)\n"
        )
        for cmd in (
            [
                "cmake",
                "-S",
                "cons",
                "-B",
                "cons/b",
                f"-DCMAKE_PREFIX_PATH={pfx}",
            ],
            ["cmake", "--build", "cons/b"],
        ):
            r = _run(cmd, proj)
            assert r.returncode == 0, (cmd, r.stdout[-1500:], r.stderr[-1500:])

    def test_the_export_is_relocatable(self, installed):
        """The build interface uses libm's resolved PATH so no target named
        `m` can shadow it (gh-1305). The INSTALL interface can use neither a
        path (wrong on the consumer's machine) nor the bare name `m` (CMake
        resolves it against the producer's targets, and `jm module m` is
        one) -- so it is the linker flag `-lm`."""
        _, pfx = installed
        text = "".join(
            p.read_text()
            for p in (pfx / "lib" / "cmake").rglob("*targets*.cmake")
        )
        assert 'INTERFACE_LINK_LIBRARIES "-lm"' in text, text[:600]
        assert "libm.so" not in text and "libm.a" not in text, text[:600]
