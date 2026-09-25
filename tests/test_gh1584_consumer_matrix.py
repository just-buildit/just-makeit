"""gh-1584: the consumer matrix -- every way a C program reaches a jm library.

One project is scaffolded, built and installed under several LAYOUTS, and a
consumer is built against each through every ROUTE, shared and static, using
only what the project advertises. The consumer then RUNS: a link that
resolves against the wrong copy, or not at all, fails here rather than in a
downstream's container.

The table is the design. A later PR widens the matrix by adding a row, not a
test: #1581 changes :data:`TARGETS` and :data:`PC_NAME`, #1580 adds a
FetchContent route, #1583 adds a layout with two projects in one prefix.

The .pc names the prefix its files were installed under, absolutely, written
at install time (gh-1582). Relocation is the CONSUMER's side of pkg-config,
and each layout says what the consumer passes:

- ``standard``: a plain install. The baseline every other row differs from.
- ``install-prefix``: configured for one prefix, installed with
  ``cmake --install --prefix`` into another. The .pc used to name the
  configured one, where nothing was ever installed.
- ``destdir``: staged with ``DESTDIR``, as a distribution package is. The .pc
  names the REAL target, not the staging dir; the consumer reads the staged
  tree with ``PKG_CONFIG_SYSROOT_DIR``.
- ``moved``: installed, then moved; the consumer passes
  ``pkg-config --define-prefix``, which derives the prefix from where the
  .pc now is (correct for ``lib/pkgconfig``; wrong for a multiarch
  ``lib/<triplet>/pkgconfig``, which is pkg-config's limitation).
- ``abs-libdir``: ``CMAKE_INSTALL_LIBDIR`` given as an absolute path, as
  GNUInstallDirs is by Nix and Guix. The .pc used to say
  ``${exec_prefix}//abs/lib64``.
- ``abs-libdir-outside``: an absolute libdir outside the prefix.
- ``system-prefix``: a prefix whose include and lib dirs pkg-config treats as
  system dirs, as /usr's are. It must emit no -I/-L for them, which it does
  only for a path spelled literally.
- the ``build-tree`` route: ``find_package`` pointed at the BUILD directory,
  with nothing installed.

Beside the matrix: the soname chain, 0.x version matching
(``SameMinorVersion``), and .pc hygiene (no empty ``Key:`` line, no blank
tail).

Lives on PROJECT_ENV_TESTS: it needs cmake, a C compiler and pkg-config.

GATE: a program linked only against what an installed jm project advertises
      builds and runs through find_package (installed and build tree) and
      pkg-config, shared and static, whether the prefix was chosen at install
      time, staged, moved or given an absolute libdir; the .pc names the
      prefix it was installed under; the shared library carries a versioned
      soname and find_package applies 0.x version matching.
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
    """Where one install's files are, and how a consumer is told."""

    #: where the files are on disk now.
    prefix: Path
    libdir: Path
    #: what the installed .pc's `prefix=` must say.
    pc_prefix: Path
    #: extra environment a pkg-config consumer sets for this layout.
    pc_env: "dict[str, str]" = {}
    #: extra pkg-config arguments a consumer passes for this layout.
    pc_args: "tuple[str, ...]" = ()
    #: a path no installed .pc or .cmake file may name.
    forbidden: "Path | None" = None
    #: False when the files are not where they were installed to (moved, or
    #: staged under DESTDIR), so the library's install name does not point
    #: at them; on macOS the consumer then sets DYLD_LIBRARY_PATH.
    in_place: bool = True


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
    layouts["standard"] = Layout(std, std / "lib", std)

    other, configured = root / "other", root / "configured"
    bp = proj / "bp"
    _build(proj, bp, configured)
    _run(["cmake", "--install", bp, "--prefix", other], proj)
    layouts["install-prefix"] = Layout(
        other, other / "lib", other, forbidden=configured
    )

    # The real target is never created; the staged copy is what exists.
    real, stage = root / "real", root / "stage"
    bd = proj / "bd"
    _build(proj, bd, real)
    env = dict(os.environ, DESTDIR=str(stage))
    _run(["cmake", "--install", bd], proj, env)
    staged = Path(str(stage) + str(real))
    layouts["destdir"] = Layout(
        staged,
        staged / "lib",
        real,
        pc_env={"PKG_CONFIG_SYSROOT_DIR": str(stage)},
        forbidden=stage,
        in_place=False,
    )

    frm, to = root / "mv_from", root / "mv_to"
    bm = proj / "bm"
    _build(proj, bm, frm)
    _run(["cmake", "--install", bm], proj)
    shutil.move(str(frm), str(to))
    layouts["moved"] = Layout(
        to, to / "lib", frm, pc_args=("--define-prefix",), in_place=False
    )

    absp = root / "absp"
    ba = proj / "ba"
    _build(proj, ba, absp, f"-DCMAKE_INSTALL_LIBDIR={absp / 'lib64'}")
    _run(["cmake", "--install", ba], proj)
    layouts["abs-libdir"] = Layout(absp, absp / "lib64", absp)

    outp, outlib = root / "outp", root / "split" / "lib"
    bo = proj / "bo"
    _build(proj, bo, outp, f"-DCMAKE_INSTALL_LIBDIR={outlib}")
    _run(["cmake", "--install", bo], proj)
    layouts["abs-libdir-outside"] = Layout(outp, outlib, outp)

    sysp = root / "sysp"
    bsy = proj / "bsy"
    _build(proj, bsy, sysp)
    _run(["cmake", "--install", bsy], proj)
    layouts["system-prefix"] = Layout(
        sysp,
        sysp / "lib",
        sysp,
        pc_env={
            "PKG_CONFIG_SYSTEM_INCLUDE_PATH": str(sysp / "include"),
            "PKG_CONFIG_SYSTEM_LIBRARY_PATH": str(sysp / "lib"),
        },
    )

    return root, proj, b, layouts


def _pc_file(lay: Layout) -> Path:
    return lay.libdir / "pkgconfig" / f"{PC_NAME}.pc"


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


def _pc_env(lay: Layout) -> "dict[str, str]":
    env = dict(os.environ)
    env["PKG_CONFIG_PATH"] = str(lay.libdir / "pkgconfig")
    env.update(lay.pc_env)
    return env


def _pkg_config(root, name, linkage, lay: Layout) -> str:
    env = _pc_env(lay)

    def pc(*args):
        cmd = ["pkg-config", *lay.pc_args, *args, PC_NAME]
        return _run(cmd, root, env).stdout.split()

    (libdir,) = pc("--variable=libdir")
    # What the consumer is told must be where the library IS.
    assert Path(libdir).resolve() == lay.libdir.resolve(), libdir
    exe = root / f"pc_{name}_{linkage}"
    if linkage == "static":
        # `-l<pkg>` finds the shared library first; a static consumer names
        # the archive, as docs/c-library.md says.
        libs = [f for f in pc("--static", "--libs") if f != f"-l{NAME}"]
        link = [str(Path(libdir) / f"lib{NAME}.a"), *libs]
    else:
        # Exactly what pkg-config hands out: no rpath. gh-1594 was hidden for
        # as long as this line added one.
        link = pc("--libs")
    _run(["cc", *pc("--cflags"), "c.c", *link, "-o", exe], root, env)
    return _run([exe], root, _loader_env(env, lay)).stdout


def _loader_env(env: "dict[str, str]", lay: Layout) -> "dict[str, str]":
    """What running the consumer needs from the dynamic loader.

    Linux: a temp prefix is on no search path, so LD_LIBRARY_PATH, the
    loader's documented way in (ld.so(8)). macOS: NOTHING for an install in
    place -- the library names itself absolutely (gh-1594), which is what is
    under test -- and DYLD_LIBRARY_PATH only where the files were moved or
    staged, so the install name points elsewhere.
    """
    env = dict(env)
    if sys.platform.startswith("linux"):
        env["LD_LIBRARY_PATH"] = str(lay.libdir)
    elif sys.platform == "darwin" and not lay.in_place:
        env["DYLD_LIBRARY_PATH"] = str(lay.libdir)
    return env


ALL_LAYOUTS = [
    "standard",
    "install-prefix",
    "destdir",
    "moved",
    "abs-libdir",
    "abs-libdir-outside",
    "system-prefix",
]
#: a system prefix's -I/-L are (rightly) filtered to nothing, so a compiler
#: given only pkg-config's output cannot find a stand-in prefix's headers.
PC_LAYOUTS = [n for n in ALL_LAYOUTS if n != "system-prefix"]


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
@pytest.mark.parametrize("layout", PC_LAYOUTS)
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


@pytest.mark.parametrize("layout", ALL_LAYOUTS)
def test_the_pc_names_the_prefix_it_was_installed_under(world, layout):
    """Absolute, and chosen at install time: not the configured prefix, not
    the staging dir, and not a path relative to the file."""
    _, _, _, layouts = world
    lay = layouts[layout]
    text = _pc_file(lay).read_text()
    assert "${pcfiledir}" not in text, text
    assert text.splitlines()[0] == f"prefix={lay.pc_prefix}", text


@pytest.mark.parametrize("layout", ["install-prefix", "destdir"])
def test_nothing_installed_names_a_path_it_was_not_installed_under(
    world, layout
):
    """Every installed text file, not just what one consumer happened to
    read: the .pc and the CMake config and targets files alike."""
    _, _, _, layouts = world
    lay = layouts[layout]
    stale = [
        str(p.relative_to(lay.prefix))
        for p in lay.prefix.rglob("*")
        if p.is_file()
        and p.suffix in (".pc", ".cmake")
        and str(lay.forbidden) in p.read_text(encoding="utf-8")
    ]
    assert stale == []


def test_the_pc_spells_an_absolute_libdir_once(world):
    """`${exec_prefix}//abs` was the shape; an absolute libdir is where the
    files are whatever the prefix, so it is written as itself."""
    _, _, _, layouts = world
    for name in ("abs-libdir", "abs-libdir-outside"):
        lay = layouts[name]
        text = _pc_file(lay).read_text()
        assert "}//" not in text, text
        assert f"libdir={lay.libdir}\n" in text, text


def test_a_system_prefix_emits_no_system_flags(world):
    """pkg-config drops -I/-L for its system dirs only when the .pc spells
    them literally. The env points its system dirs at the stand-in prefix,
    as /usr's are by default."""
    root, _, _, layouts = world
    lay = layouts["system-prefix"]
    cmd = ["pkg-config", "--cflags", "--libs", PC_NAME]
    out = _run(cmd, root, _pc_env(lay)).stdout.split()
    assert not [f for f in out if f[:2] in ("-I", "-L")], out
    assert f"-l{NAME}" in out, out


@pytest.mark.parametrize("layout", ALL_LAYOUTS)
def test_the_pc_has_no_empty_field_or_blank_tail(world, layout):
    """An optional field with nothing to say is left out rather than written
    as `URL:`, and the template's empty slots leave no blank lines."""
    _, _, _, layouts = world
    text = _pc_file(layouts[layout]).read_text()
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
        # gh-1594: the install name is absolute -- not @rpath/..., which a
        # program linked by pkg-config cannot load.
        idn = _run(["otool", "-D", real], lib).stdout.splitlines()[-1]
        assert idn.startswith("/"), idn
        assert Path(idn).name == f"lib{NAME}.{ABI}.dylib", idn
        assert os.path.samefile(Path(idn).parent, lib), idn
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
