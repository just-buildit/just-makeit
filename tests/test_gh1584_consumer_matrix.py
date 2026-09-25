"""gh-1584: the consumer matrix -- every way a C program reaches a jm library.

One project is scaffolded, built and installed under several LAYOUTS, and a
consumer is built against each through every ROUTE, shared and static, using
only what the project advertises. The consumer then RUNS: a link that
resolves against the wrong copy, or not at all, fails here rather than in a
downstream's container.

The table is the design. A later PR widens the matrix by adding a row, not a
test: #1581 changes :data:`TARGETS` and :data:`PC_NAME`, #1580 adds a
FetchContent route, #1583 adds a layout with two projects in one prefix.

Rows (gh-1582):

- ``standard``: a plain install. The baseline every other row differs from.
- ``install-prefix``: configured for one prefix, installed with
  ``cmake --install --prefix`` into another -- CMake's documented way to
  choose the prefix at install time. The ``.pc`` used to name the
  configured one, where nothing was ever installed.
- ``relocated``: installed into a staging prefix, then MOVED. The staging
  path no longer exists, so a ``.pc`` or CMake config that remembered it
  fails to compile or link, and the test also asserts the text is gone.
- ``abs-libdir``: ``CMAKE_INSTALL_LIBDIR`` given as an absolute path under
  the prefix, as GNUInstallDirs is by Nix and Guix. The ``.pc`` used to say
  ``${exec_prefix}//abs/lib64``.
- ``abs-libdir-outside``: an absolute libdir OUTSIDE the prefix (a split
  output). The ``.pc`` must spell it absolutely rather than relative to a
  prefix it is not under.
- the ``build-tree`` route: ``find_package`` pointed at the BUILD directory,
  with nothing installed.

Beside the matrix: the soname chain (a versioned shared library), and 0.x
version matching (``SameMinorVersion``: 0.1.1 accepts 0.1.3, 0.0.5 and 0.2
do not).

Lives on PROJECT_ENV_TESTS: it needs cmake, a C compiler and pkg-config.

GATE: a program linked only against what an installed jm project advertises
      builds and runs through find_package (installed and build tree) and
      pkg-config, shared and static, from a moved prefix and under an
      absolute libdir; the shared library carries a versioned soname and
      find_package applies 0.x version matching.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from _jmrun import run_cli

NAME = "demo"
VERSION = "0.1.3"
#: the pkg-config module a consumer names (#1581 renames it).
PC_NAME = NAME
#: linkage -> the exported CMake target (#1581 renames these).
TARGETS = {
    "shared": f"{NAME}::{NAME}_lib",
    "static": f"{NAME}::{NAME}_lib_static",
}

_CONSUMER = (
    "#include <stdio.h>\n"
    '#include "gain/gain_core.h"\n'
    "int main(void) {\n"
    "    gain_state_t *g = gain_create(1.0f);\n"
    "    float y = gain_step(g, 4.0f);\n"
    "    gain_destroy(g);\n"
    '    printf("ran %d\\n", y == y);\n'
    "    return 0;\n"
    "}\n"
)


def _run(cmd, cwd, env=None, ok=True):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True
    )
    if ok:
        assert r.returncode == 0, (cmd, r.stdout[-2500:], r.stderr[-2500:])
    return r


class Layout(NamedTuple):
    """Where one install puts things, as a consumer must be told."""

    prefix: Path
    libdir: Path
    #: the path an install once used and must no longer be referenced.
    forbidden: "Path | None" = None


def _build(proj: Path, bdir: Path, pfx: Path, *extra: str) -> None:
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            bdir,
            "-DBUILD_PYTHON=OFF",
            f"-DCMAKE_INSTALL_PREFIX={pfx}",
            *extra,
        ],
        proj,
    )
    _run(["cmake", "--build", bdir], proj)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("gh1584")
    r = run_cli(
        "new",
        NAME,
        "--object",
        "gain",
        "--state",
        "level:float:1.0",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    proj = root / NAME
    # A patch release, so version matching has a lower patch to accept.
    cm = proj / "CMakeLists.txt"
    text = cm.read_text(encoding="utf-8")
    assert len(re.findall(r"VERSION 0\.1\.0\b", text)) == 1
    cm.write_text(text.replace("VERSION 0.1.0", f"VERSION {VERSION}"))
    (root / "c.c").write_text(_CONSUMER)

    layouts: "dict[str, Layout]" = {}

    std = root / "std"
    b = proj / "b"
    _build(proj, b, std)
    _run(["cmake", "--install", b], proj)
    layouts["standard"] = Layout(std, std / "lib")

    # `cmake --install --prefix` overrides the configured prefix at install
    # time; a .pc that baked the configured one in points at a tree that was
    # never installed.
    other, configured = root / "other", root / "configured"
    bp = proj / "bp"
    _build(proj, bp, configured)
    _run(["cmake", "--install", bp, "--prefix", other], proj)
    layouts["install-prefix"] = Layout(other, other / "lib", configured)

    # Built for the staging prefix, installed there, then moved away from it.
    stage, moved = root / "stage", root / "moved"
    bs = proj / "bs"
    _build(proj, bs, stage)
    _run(["cmake", "--install", bs], proj)
    shutil.move(str(stage), str(moved))
    layouts["relocated"] = Layout(moved, moved / "lib", forbidden=stage)

    absp = root / "absp"
    ba = proj / "ba"
    _build(proj, ba, absp, f"-DCMAKE_INSTALL_LIBDIR={absp / 'lib64'}")
    _run(["cmake", "--install", ba], proj)
    layouts["abs-libdir"] = Layout(absp, absp / "lib64")

    outp, outlib = root / "outp", root / "split" / "lib"
    bo = proj / "bo"
    _build(proj, bo, outp, f"-DCMAKE_INSTALL_LIBDIR={outlib}")
    _run(["cmake", "--install", bo], proj)
    layouts["abs-libdir-outside"] = Layout(outp, outlib)

    # A prefix declared a SYSTEM prefix, standing in for /usr: its .pc must
    # be written absolutely, or pkg-config cannot filter its system dirs.
    sysp = root / "sysp"
    bsy = proj / "bsy"
    _build(proj, bsy, sysp, f"-DJM_PC_SYSTEM_PREFIXES={sysp}")
    _run(["cmake", "--install", bsy], proj)
    layouts["system-prefix"] = Layout(sysp, sysp / "lib")

    return root, proj, b, layouts


def _find_package(root, name, linkage, where: "list[str]") -> str:
    cons = root / f"fp_{name}_{linkage}"
    cons.mkdir()
    (cons / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(cons C)\n"
        f"find_package({NAME} {VERSION} REQUIRED)\n"
        "add_executable(c ../c.c)\n"
        f"target_link_libraries(c PRIVATE {TARGETS[linkage]})\n"
    )
    _run(["cmake", "-S", ".", "-B", "b", *where], cons)
    _run(["cmake", "--build", "b"], cons)
    return _run([cons / "b" / "c"], cons).stdout


def _pkg_config(root, name, linkage, lay: Layout) -> str:
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(lay.libdir / "pkgconfig")

    def pc(*args):
        return _run(["pkg-config", *args, PC_NAME], root, env).stdout.split()

    (libdir,) = pc("--variable=libdir")
    # What the .pc says must be where the library IS, not where it was.
    assert Path(libdir).resolve() == lay.libdir.resolve(), libdir
    exe = root / f"pc_{name}_{linkage}"
    if linkage == "static":
        # `-l<pkg>` finds the shared library first; a static consumer names
        # the archive, as docs/c-library.md says.
        libs = [f for f in pc("--static", "--libs") if f != f"-l{NAME}"]
        link = [str(Path(libdir) / f"lib{NAME}.a"), *libs]
    else:
        link = [*pc("--libs"), f"-Wl,-rpath,{libdir}"]
    flags = [*pc("--cflags"), *link]
    if lay.forbidden:
        assert not any(str(lay.forbidden) in f for f in flags), flags
    _run(["cc", *pc("--cflags"), "c.c", *link, "-o", exe], root, env)
    return _run([exe], root, env).stdout


LAYOUT_NAMES = [
    "standard",
    "install-prefix",
    "relocated",
    "abs-libdir",
    "abs-libdir-outside",
]
#: every layout; pkg-config consumers build against LAYOUT_NAMES only,
#: because a system prefix's flags are (rightly) filtered to nothing.
ALL_LAYOUTS = [*LAYOUT_NAMES, "system-prefix"]


@pytest.mark.parametrize("linkage", ["shared", "static"])
@pytest.mark.parametrize("layout", ALL_LAYOUTS)
def test_find_package_installed(world, layout, linkage):
    root, _, _, layouts = world
    lay = layouts[layout]
    where = [
        f"-DCMAKE_PREFIX_PATH={lay.prefix}",
        f"-D{NAME}_DIR={lay.libdir / 'cmake' / NAME}",
    ]
    out = _find_package(root, layout, linkage, where)
    assert out == "ran 1\n", out


@pytest.mark.parametrize("linkage", ["shared", "static"])
@pytest.mark.parametrize("layout", LAYOUT_NAMES)
def test_pkg_config(world, layout, linkage):
    root, _, _, layouts = world
    out = _pkg_config(root, layout, linkage, layouts[layout])
    assert out == "ran 1\n", out


@pytest.mark.parametrize("linkage", ["shared", "static"])
def test_find_package_build_tree(world, linkage):
    """Nothing installed: `<pkg>_DIR` is the build directory itself."""
    root, _, b, _ = world
    out = _find_package(root, "build-tree", linkage, [f"-D{NAME}_DIR={b}"])
    assert out == "ran 1\n", out


@pytest.mark.parametrize("layout", ["relocated", "install-prefix"])
def test_a_moved_prefix_names_nothing_from_where_it_was(world, layout):
    """Every installed text file, not just what one consumer happened to
    read: the .pc and the CMake config and targets files alike."""
    _, _, _, layouts = world
    lay = layouts[layout]
    assert not lay.forbidden.exists()
    stale = [
        str(p.relative_to(lay.prefix))
        for p in lay.prefix.rglob("*")
        if p.is_file()
        and p.suffix in (".pc", ".cmake")
        and str(lay.forbidden) in p.read_text(encoding="utf-8")
    ]
    assert stale == []


def test_the_pc_spells_an_absolute_libdir_once(world):
    """`${exec_prefix}//abs` was the shape; an absolute libdir is written as
    itself, and one under the prefix still relocates."""
    _, _, _, layouts = world
    for name in ("abs-libdir", "abs-libdir-outside"):
        lay = layouts[name]
        text = (lay.libdir / "pkgconfig" / f"{PC_NAME}.pc").read_text()
        assert "}//" not in text, text
    inside = layouts["abs-libdir"].libdir / "pkgconfig" / f"{PC_NAME}.pc"
    assert "libdir=${exec_prefix}/lib64" in inside.read_text()


def test_a_system_prefix_emits_no_system_flags(world):
    """Under a system prefix pkg-config must be able to drop -I/-L: it does
    so only for a LITERAL system dir, and `${pcfiledir}/../..` is not one.
    The env points pkg-config's system dirs at the stand-in prefix, as
    /usr's are by default."""
    root, _, _, layouts = world
    lay = layouts["system-prefix"]
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(lay.libdir / "pkgconfig")
    env["PKG_CONFIG_SYSTEM_INCLUDE_PATH"] = str(lay.prefix / "include")
    env["PKG_CONFIG_SYSTEM_LIBRARY_PATH"] = str(lay.libdir)
    out = _run(
        ["pkg-config", "--cflags", "--libs", PC_NAME], root, env
    ).stdout.split()
    assert not [f for f in out if f[:2] in ("-I", "-L")], out
    assert f"-l{NAME}" in out, out


@pytest.mark.parametrize("layout", ALL_LAYOUTS)
def test_the_pc_has_no_empty_field_or_blank_tail(world, layout):
    """An optional field with nothing to say is left out rather than written
    as `URL:`, and the template's empty slots leave no blank lines."""
    _, _, _, layouts = world
    text = (layouts[layout].libdir / "pkgconfig" / f"{PC_NAME}.pc").read_text()
    empty = [ln for ln in text.splitlines() if re.fullmatch(r"[\w.]+:\s*", ln)]
    assert empty == [], empty
    assert text.endswith("\n") and not text.endswith("\n\n"), repr(text[-40:])


ABI = ".".join(VERSION.split(".")[:2])


def test_the_shared_library_has_a_versioned_soname(world):
    """lib<pkg>.so.X.Y.Z, soname lib<pkg>.so.<ABI>, and the dev link; under
    0.x the ABI is major.minor, as the version file's matching is."""
    _, _, _, layouts = world
    lib = layouts["standard"].libdir
    if sys.platform.startswith("linux"):
        real = lib / f"lib{NAME}.so.{VERSION}"
        assert real.is_file() and not real.is_symlink()
        assert os.readlink(lib / f"lib{NAME}.so.{ABI}") == real.name
        assert (lib / f"lib{NAME}.so").resolve() == real.resolve()
        dyn = _run(["readelf", "-d", real], lib).stdout
        assert f"[lib{NAME}.so.{ABI}]" in dyn, dyn
    elif sys.platform == "darwin":
        real = lib / f"lib{NAME}.{VERSION}.dylib"
        assert real.is_file() and not real.is_symlink()
        idn = _run(["otool", "-D", real], lib).stdout
        assert f"@rpath/lib{NAME}.{ABI}.dylib" in idn, idn
    else:
        pytest.fail(f"no soname check written for {sys.platform}")


@pytest.mark.parametrize(
    "request_, found",
    [
        ("0.1.1", True),  # same minor, lower patch
        (VERSION, True),
        ("0.0.5", False),  # an older minor: 0.x breaks at minor releases
        ("0.2", False),
    ],
)
def test_zero_x_version_matching(world, request_, found):
    root, _, _, layouts = world
    lay = layouts["standard"]
    cons = root / f"ver_{request_}"
    cons.mkdir()
    (cons / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(ver C)\n"
        f"find_package({NAME} {request_} QUIET)\n"
        f'message(STATUS "FOUND=${{{NAME}_FOUND}}")\n'
    )
    out = _run(
        ["cmake", "-S", ".", "-B", "b", f"-DCMAKE_PREFIX_PATH={lay.prefix}"],
        cons,
    ).stdout
    assert f"FOUND={1 if found else 0}" in out, out
