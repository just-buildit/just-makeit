"""gh-1572: the combined libraries carry their cores' external link deps.

The root folds each core into ``lib<pkg>`` by its objects alone --
``$<TARGET_OBJECTS:>`` -- which is objects, not a link edge, so a core's
``extra_link_libs`` never reached the combined libraries. The shared one
was linked with those symbols undefined: macOS and Windows refused
(``nco_tone`` and ``kitchen_sink`` against doppler, found by gh-1377), and
Linux accepted it, leaving the failure to the first C consumer of the
``.so``. The archive's link interface did not carry them either.

This builds a tiny external CMake package, a jm project whose cores call
into it -- one per place jm emits a core's link line: a standalone object,
a module object, and a module's collocated object -- installs it, and links
a C consumer against the INSTALLED project alone, shared and static,
through ``find_package``. The consumer names no external package itself:
the shared library must have resolved it, and the archive's interface and
the installed config must hand it on.

Lives on PROJECT_ENV_TESTS: it needs cmake and a C compiler.

GATE: a core's extra_link_libs reach both combined libraries and the
      installed config, so an installed project's consumer links, shared and
      static, without naming the dependency itself.
"""

from __future__ import annotations

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
target_include_directories(extdep PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
    $<INSTALL_INTERFACE:include>)
install(TARGETS extdep EXPORT ExtDepTargets ARCHIVE DESTINATION lib)
install(FILES extdep.h DESTINATION include)
install(EXPORT ExtDepTargets NAMESPACE ExtDep::
        FILE ExtDepConfig.cmake DESTINATION lib/cmake/ExtDep)
"""

# The three cores, and which manifest table their extra_link_libs live in:
# a standalone object's own, a module object's own, and -- for the object
# collocated with its module (same name as the module) -- the module's.
_CORES = ("alpha", "beta", "grp")

_CONSUMER = "".join(f"int {c}_ext(int);\n" for c in _CORES) + (
    "int main(void) {\n"
    "    return "
    + " + ".join(f"{c}_ext(1)" for c in _CORES)
    + " == 2 * "
    + str(len(_CORES))
    + " ? 0 : 1;\n"
    "}\n"
)


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-2500:], r.stderr[-2500:])
    return r


def _cmake_install(src: Path, build: Path, pfx: Path, *extra: str) -> None:
    _run(
        [
            "cmake",
            "-S",
            str(src),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={pfx}",
            *extra,
        ],
        src,
    )
    _run(["cmake", "--build", str(build)], src)
    _run(["cmake", "--install", str(build)], src)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")

    ext = root / "extdep"
    ext.mkdir()
    (ext / "CMakeLists.txt").write_text(_EXT_CMAKE)
    (ext / "extdep.h").write_text("int extdep_twice(int x);\n")
    (ext / "extdep.c").write_text(
        '#include "extdep.h"\nint extdep_twice(int x) { return 2 * x; }\n'
    )
    ext_pfx = root / "ext_pfx"
    _cmake_install(ext, root / "ext_b", ext_pfx)

    assert run_cli("new", "jmx", "--object", "alpha", cwd=root).returncode == 0
    proj = root / "jmx"
    for args in (
        ("module", "grp"),
        ("object", "beta", "--module", "grp"),
        ("object", "grp", "--module", "grp"),
    ):
        r = run_cli(*args, cwd=proj)
        assert r.returncode == 0, r.stdout + r.stderr

    cfg = C.load(proj)
    cfg["project"]["find_packages"] = ["ExtDep"]
    for table in (cfg["alpha"], cfg["beta"], cfg["module"]["grp"]):
        table["extra_link_libs"] = ["ExtDep::extdep"]
    # extra_link_libs may name one of the project's own cores (kitchen_sink
    # links a c_deps `cjson_core` this way). Its objects are already in both
    # libraries, so restating it would export a target in no export set.
    cfg["alpha"]["extra_link_libs"].append("beta_core")
    # gh-1613: doppler's shape -- a c_dep OBJECT library NOT named `_core`,
    # which the root folds into both libraries, named by its objects and by
    # its bare target. Either restated on the combined libraries puts its
    # objects in lib<pkg>.so twice: `multiple definition of ...`.
    for d, obj in (("util", "util_obj"), ("more", "more_objs")):
        (proj / "native" / "src" / d).mkdir()
        (proj / "native" / "src" / d / f"{d}.c").write_text(
            f"int jmx_{d}_one(void) {{ return 1; }}\n"
        )
        (proj / "native" / "src" / d / "CMakeLists.txt").write_text(
            f"add_library({obj} OBJECT {d}.c)\n"
            f"set_target_properties({obj} PROPERTIES"
            " POSITION_INDEPENDENT_CODE ON)\n"
        )
    cfg["project"]["c_deps"] = ["util", "more"]
    cfg["alpha"]["extra_link_libs"] += [
        "$<TARGET_OBJECTS:util_obj>",  # doppler's dp_interrupt_obj
        "more_objs",  # the bare target
    ]
    # the module-object emit site too (`_object`, not `_init`)
    cfg["beta"]["extra_link_libs"].append("more_objs")
    C.save(proj, cfg)
    # ...and the root folds both into the combined libraries itself, as
    # doppler's does -- the state in which restating them doubles them.
    root_cmake = proj / "CMakeLists.txt"
    root_cmake.write_text(
        root_cmake.read_text()
        + "".join(
            f"target_sources(jmx_{lib} PRIVATE $<TARGET_OBJECTS:{obj}>)\n"
            for lib in ("lib", "lib_static")
            for obj in ("util_obj", "more_objs")
        )
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    for core in _CORES:
        (src,) = proj.glob(f"native/src/*/{core}_core.c")
        src.write_text(
            src.read_text()
            + '\n#include "extdep.h"\n'
            + f"int {core}_ext(int x) {{ return extdep_twice(x); }}\n"
        )

    pfx = root / "pfx"
    _cmake_install(
        proj,
        proj / "b",
        pfx,
        "-DBUILD_PYTHON=OFF",
        f"-DCMAKE_PREFIX_PATH={ext_pfx}",
    )
    return root, proj, pfx, ext_pfx


def test_every_emit_site_links_the_combined_libraries(installed):
    """Each shape's generated CMakeLists restates the dep on both libs."""
    _, proj, _, _ = installed
    for core in _CORES:
        (cm,) = [
            p
            for p in proj.glob("native/src/*/CMakeLists.txt")
            if f"add_library({core}_core" in p.read_text()
        ]
        text = cm.read_text()
        for lib, scope in (("_lib", "PRIVATE"), ("_lib_static", "PUBLIC")):
            assert (
                f"target_link_libraries(${{PROJECT_NAME}}{lib} {scope}\n"
                "      ExtDep::extdep)" in text
            ), (core, cm, text)


@pytest.mark.parametrize("target", ["jmx", "jmx-static"])
def test_an_installed_consumer_links(installed, target):
    root, _, pfx, ext_pfx = installed
    cons = root / f"cons_{target}"
    cons.mkdir()
    (cons / "c.c").write_text(_CONSUMER)
    (cons / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(cons C)\n"
        "find_package(jmx REQUIRED)\n"
        "add_executable(c c.c)\n"
        f"target_link_libraries(c PRIVATE jmx::{target})\n"
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
    )
    _run(["cmake", "--build", "b"], cons)
