"""gh-1576: an installed project's header may include a dependency's header.

nco_tone's ``tone_core.h`` includes doppler's ``nco/nco_core.h``. A consumer
of the INSTALLED project then needs the dependency's compile usage -- include
dirs, definitions, options -- just to compile that header, whether it links
shared or static, through ``find_package`` or ``pkg-config``. Measured on
main after gh-1572/gh-1573, four of eight faces failed::

    alpha_core.h: fatal error: extdep.h: No such file or directory

- ``find_package`` + shared: the dependency was linked PRIVATE, which
  resolves its symbols and passes on nothing. Fixed by handing its compile
  usage on without the link (``_libwiring._compile_usage_c``).
- ``pkg-config``, a ``find_packages`` dependency: nothing reached the
  ``.pc``. pc(5) puts a dependency that ships a ``.pc`` in
  ``Requires.private`` (its Cflags always, its Libs with ``--static``); the
  author names its module with ``pkg_config`` because a CMake package name
  maps to none.

The dependency here ``#error``s without its interface definition, so a fix
that passes include dirs alone -- measured, and it does not -- fails too.
Each consumer also bumps the dependency's counter through jm and directly:
``1,2`` is one copy of its state, which re-linking a static dependency into
the consumer could break.

Lives on PROJECT_ENV_TESTS: it needs cmake, a C compiler and pkg-config.

GATE: a consumer of an installed project whose header includes a
      dependency's header compiles, links and shares one copy of the
      dependency -- find_package and pkg-config, shared and static, for a
      find_packages and a pkg_modules dependency alike.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

_EXT_CMAKE = r"""
cmake_minimum_required(VERSION 3.16)
project(ExtDep C)
add_library(extdep STATIC extdep.c)
set_target_properties(extdep PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(extdep PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:include>)
target_compile_definitions(extdep INTERFACE EXTDEP_API=1)
install(TARGETS extdep EXPORT ExtDepTargets ARCHIVE DESTINATION lib)
install(FILES extdep.h DESTINATION include)
install(EXPORT ExtDepTargets NAMESPACE ExtDep::
        FILE ExtDepConfig.cmake DESTINATION lib/cmake/ExtDep)
file(WRITE ${CMAKE_BINARY_DIR}/extdep.pc
"prefix=${CMAKE_INSTALL_PREFIX}
libdir=\${prefix}/lib
includedir=\${prefix}/include
Name: extdep
Description: a dependency with a CMake config and a .pc
Version: 1.0
Libs: -L\${libdir} -lextdep
Cflags: -I\${includedir} -DEXTDEP_API=1
")
install(FILES ${CMAKE_BINARY_DIR}/extdep.pc DESTINATION lib/pkgconfig)
"""

_EXT_H = (
    "#ifndef EXTDEP_API\n"
    '#error "EXTDEP_API: the dependency usage requirements did not arrive"\n'
    "#endif\n"
    "typedef struct { int k; } extdep_cfg_t;\n"
    "int extdep_twice(int x);\n"
    "int extdep_bump(void);\n"
)
_EXT_C = (
    "#define EXTDEP_API 1\n"
    '#include "extdep.h"\n'
    "static int n;\n"
    "int extdep_twice(int x) { return 2 * x; }\n"
    "int extdep_bump(void) { return ++n; }\n"
)

# Compiles the jm header (which includes extdep.h), and bumps the
# dependency's counter through jm then directly: "1,2" is one copy.
_CONSUMER = (
    "#include <stdio.h>\n"
    '#include "alpha/alpha_core.h"\n'
    "int main(void) {\n"
    "    extdep_cfg_t c = {1};\n"
    "    int a = alpha_bump(), b = extdep_bump();\n"
    '    printf("%d,%d", a, b);\n'
    "    return alpha_cfg_k(&c) == 2 ? 0 : 1;\n"
    "}\n"
)

#: project -> (the [project] declaration, the target a core links)
_STYLES = {
    "pa": (
        {"find_packages": [{"name": "ExtDep", "pkg_config": "extdep"}]},
        "ExtDep::extdep",
    ),
    "pb": ({"pkg_modules": ["extdep"]}, "PkgConfig::EXTDEP"),
}


def _run(cmd, cwd, env):
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


def _project(root: Path, name: str, env: dict, ext_pfx: Path) -> Path:
    decl, target = _STYLES[name]
    assert run_cli("new", name, "--object", "alpha", cwd=root).returncode == 0
    proj = root / name
    cfg = C.load(proj)
    cfg["project"].update(decl)
    # A plain library name and a PATH beside the target, as real manifests
    # carry: the compile-usage lines must read properties of neither -- a
    # path inside `$<TARGET_EXISTS:>` fails configure outright.
    cfg["alpha"]["extra_link_libs"] = [target, "m", "${JM_MATH_LIBRARY}"]
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    h = proj / "native" / "inc" / "alpha" / "alpha_core.h"
    s = h.read_text()
    anchor = '#include "clib_common.h"'
    assert s.count(anchor) == 1, s
    h.write_text(
        s.replace(anchor, anchor + '\n#include "extdep.h"')
        + "\nint alpha_cfg_k(const extdep_cfg_t *c);\nint alpha_bump(void);\n"
    )
    core = proj / "native" / "src" / "alpha" / "alpha_core.c"
    core.write_text(
        core.read_text() + "\nint alpha_cfg_k(const extdep_cfg_t *c)"
        " { return extdep_twice(c->k); }\n"
        "int alpha_bump(void) { return extdep_bump(); }\n"
    )
    pfx = root / f"{name}_pfx"
    _cmake_install(
        proj,
        pfx,
        env,
        "-DBUILD_PYTHON=OFF",
        f"-DCMAKE_PREFIX_PATH={ext_pfx}",
    )
    return pfx


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    ext, ext_pfx = root / "extdep", root / "ext_pfx"
    ext.mkdir()
    (ext / "CMakeLists.txt").write_text(_EXT_CMAKE)
    (ext / "extdep.h").write_text(_EXT_H)
    (ext / "extdep.c").write_text(_EXT_C)
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(ext_pfx / "lib" / "pkgconfig")
    _cmake_install(ext, ext_pfx, env)
    (root / "c.c").write_text(_CONSUMER)
    pfxs = {name: _project(root, name, env, ext_pfx) for name in _STYLES}
    return root, ext_pfx, pfxs, env


def _consume_cmake(root, ext_pfx, name, pfx, target, env) -> str:
    cons = root / f"cons_{name}_{target}"
    cons.mkdir()
    (cons / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(cons C)\n"
        f"find_package({name} REQUIRED)\n"
        "add_executable(c ../c.c)\n"
        f"target_link_libraries(c PRIVATE {name}::{target})\n"
    )
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "b",
            f"-DCMAKE_PREFIX_PATH={pfx};{ext_pfx}",
        ],
        cons,
        env,
    )
    _run(["cmake", "--build", "b"], cons, env)
    run_env = dict(env, LD_LIBRARY_PATH=str(pfx / "lib"))
    return _run([str(cons / "b" / "c")], cons, run_env).stdout


def _consume_pc(root, name, pfx, static, env) -> str:
    pc_env = dict(env)
    pc_env["PKG_CONFIG_PATH"] = os.pathsep.join(
        [str(pfx / "lib" / "pkgconfig"), env["PKG_CONFIG_PATH"]]
    )

    def pc(*args):
        return _run(["pkg-config", *args, name], root, pc_env).stdout.split()

    exe = root / f"pc_{name}_{'static' if static else 'shared'}"
    if static:
        libs = [f for f in pc("--static", "--libs") if f != f"-l{name}"]
        link = [str(pfx / "lib" / f"lib{name}.a"), *libs]
    else:
        link = pc("--libs")
    _run(["cc", *pc("--cflags"), "c.c", *link, "-o", str(exe)], root, pc_env)
    run_env = dict(pc_env, LD_LIBRARY_PATH=str(pfx / "lib"))
    return _run([str(exe)], root, run_env).stdout


@pytest.mark.parametrize("name", sorted(_STYLES))
@pytest.mark.parametrize("linkage", ["shared", "static"])
def test_find_package_consumer(installed, name, linkage):
    root, ext_pfx, pfxs, env = installed
    target = f"{name}_lib" if linkage == "shared" else f"{name}_lib_static"
    out = _consume_cmake(root, ext_pfx, name, pfxs[name], target, env)
    assert out == "1,2", out


@pytest.mark.parametrize("name", sorted(_STYLES))
@pytest.mark.parametrize("linkage", ["shared", "static"])
def test_pkg_config_consumer(installed, name, linkage):
    root, _, pfxs, env = installed
    out = _consume_pc(root, name, pfxs[name], linkage == "static", env)
    assert out == "1,2", out


def test_the_export_carries_no_absolute_dependency_path(installed):
    """The compile usage is exported as expressions the consumer evaluates,
    never as the producer's paths (cmake-packages(7), relocatability)."""
    root, ext_pfx, pfxs, _ = installed
    for pfx in pfxs.values():
        text = "".join(
            p.read_text() for p in (pfx / "lib" / "cmake").rglob("*.cmake")
        )
        assert str(ext_pfx) not in text, text[:1500]
