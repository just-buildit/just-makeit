"""gh-1573: the installed ``.pc`` names the pkg-config modules it depends on.

gh-1572 made both combined libraries carry a core's ``extra_link_libs`` and
the installed CMake config ``find_dependency()`` them, so a ``find_package``
consumer links shared and static. The ``.pc`` still said only
``-l<pkg> -lm``, so ``pkg-config --static --libs <pkg>`` left the archive's
calls into the dependency unresolved::

    alpha_core.c: undefined reference to `extdep_twice'

A ``[project] pkg_modules`` entry IS a pkg-config module name, so it goes to
``Requires.private``, which ``--static`` follows. A ``find_packages`` entry
names a CMake package, which maps to no pkg-config module, so it is not
guessed at: a project that wants the pkg-config face complete declares the
dependency through ``pkg_modules`` (docs/c-library.md).

Lives on PROJECT_ENV_TESTS: it needs cmake, a C compiler and pkg-config.

GATE: a pkg_modules dependency reaches the installed .pc as
      Requires.private, so a static pkg-config consumer links.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

_EXT_CMAKE = """\
cmake_minimum_required(VERSION 3.16)
project(ExtDep C)
add_library(extdep STATIC extdep.c)
set_target_properties(extdep PROPERTIES POSITION_INDEPENDENT_CODE ON)
install(TARGETS extdep ARCHIVE DESTINATION lib)
install(FILES extdep.h DESTINATION include)
file(WRITE ${CMAKE_BINARY_DIR}/extdep.pc
"prefix=${CMAKE_INSTALL_PREFIX}
libdir=\\${prefix}/lib
includedir=\\${prefix}/include
Name: extdep
Description: a dependency that ships a .pc
Version: 1.0
Libs: -L\\${libdir} -lextdep
Cflags: -I\\${includedir}
")
install(FILES ${CMAKE_BINARY_DIR}/extdep.pc DESTINATION lib/pkgconfig)
"""

_CONSUMER = "int alpha_ext(int);\nint main(void) { return alpha_ext(1) == 2 ? 0 : 1; }\n"


def _run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-2500:], r.stderr[-2500:])
    return r


def _cmake_install(src: Path, pfx: Path, env: dict, *extra: str) -> None:
    b = src / "b"
    _run(
        ["cmake", "-S", ".", "-B", str(b), f"-DCMAKE_INSTALL_PREFIX={pfx}"]
        + list(extra),
        src,
        env,
    )
    _run(["cmake", "--build", str(b)], src, env)
    _run(["cmake", "--install", str(b)], src, env)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    ext, ext_pfx, pfx = root / "extdep", root / "ext_pfx", root / "pfx"
    ext.mkdir()
    (ext / "CMakeLists.txt").write_text(_EXT_CMAKE)
    (ext / "extdep.h").write_text("int extdep_twice(int x);\n")
    (ext / "extdep.c").write_text(
        '#include "extdep.h"\nint extdep_twice(int x) { return 2 * x; }\n'
    )
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(ext_pfx / "lib" / "pkgconfig")
    _cmake_install(ext, ext_pfx, env)

    assert run_cli("new", "jmx", "--object", "alpha", cwd=root).returncode == 0
    proj = root / "jmx"
    cfg = C.load(proj)
    cfg["project"]["pkg_modules"] = ["extdep"]
    cfg["alpha"]["extra_link_libs"] = ["PkgConfig::EXTDEP"]
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    core = proj / "native" / "src" / "alpha" / "alpha_core.c"
    core.write_text(
        core.read_text()
        + '\n#include "extdep.h"\n'
        + "int alpha_ext(int x) { return extdep_twice(x); }\n"
    )
    _cmake_install(proj, pfx, env, "-DBUILD_PYTHON=OFF")

    env["PKG_CONFIG_PATH"] = os.pathsep.join(
        [str(pfx / "lib" / "pkgconfig"), env["PKG_CONFIG_PATH"]]
    )
    (root / "c.c").write_text(_CONSUMER)
    return root, pfx, env


def test_the_pc_requires_the_module(installed):
    _, pfx, _ = installed
    pc = (pfx / "lib" / "pkgconfig" / "jmx.pc").read_text()
    assert "\nRequires.private: extdep\n" in pc, pc


def test_a_static_pkg_config_consumer_links(installed):
    """The archive plus exactly what ``pkg-config --static`` reports."""
    root, pfx, env = installed
    libs = _run(
        ["pkg-config", "--static", "--libs", "jmx"], root, env
    ).stdout.split()
    archive = str(pfx / "lib" / "libjmx.a")
    _run(
        ["cc", "c.c", archive, *[f for f in libs if f != "-ljmx"], "-o", "cs"],
        root,
        env,
    )
    _run([str(root / "cs")], root, env)
