"""gh-1599: a public link flag or definition reaches every face.

doppler's installed headers are inline code and header-only threading that
call pthread, and they name ``MAP_ANONYMOUS``, which glibc hides unless
``_GNU_SOURCE`` is defined before libc is first reached. So a CONSUMER needs
``-lpthread`` on its link and ``-D_GNU_SOURCE`` on its compile, and so does
the project's own build. jm had keys only for a dependency's private face;
doppler hand-wrote both, which is part of why it could not adopt jm's
packaging.

``[project] public_link_libs`` and ``public_defines`` now reach six faces:
the project's own compile (definitions, project-wide), its executables and
extensions (link flags, every target after the external-deps block), both
combined libraries' PUBLIC link and definitions, the exported targets
(which carry PUBLIC), and the installed ``.pc``'s ``Libs:`` and ``Cflags:``.

The link flag here is a real one, not ``-lpthread``: glibc 2.34 moved
pthread into libc, so a missing ``-lpthread`` cannot fail a link on this
box and a test of it would pass whatever jm did. ``-L<dir> -lxtra`` names a
tiny static library the package's header calls inline.

Lives on PROJECT_ENV_TESTS: it builds, installs and links consumers. What
needs no build -- refusals, removal, `jm script` -- is in
``tests/test_gh1599_public_flags_manifest.py``.

GATE: `[project] public_link_libs` and `public_defines` reach the project's
      own compile and executables, both combined libraries' public face, the
      exported targets and the installed .pc -- consumed by pkg-config and
      find_package, shared and static, every program run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

_XTRA_C = "int xtra_value(void) { return 42; }\n"

# The package's public header: it needs the definition to compile at all,
# and its inline function makes the CONSUMER's object reference libxtra.
_API_H = (
    "#ifndef PUB_API_H\n"
    "#define PUB_API_H\n"
    "#ifndef _GNU_SOURCE\n"
    '#error "_GNU_SOURCE: the public definition did not arrive"\n'
    "#endif\n"
    "int xtra_value(void);\n"
    "static inline int pub_inline(void) { return xtra_value(); }\n"
    "int pub_answer(void);\n"
    "#endif\n"
)
# The core includes it too (so its own compile needs the definition) and
# calls libxtra (so every executable linking the core needs the flag).
_CORE_ADD = (
    '\n#include "pub/pub_api.h"\n'
    "int pub_answer(void) { return xtra_value(); }\n"
)
_CONSUMER = (
    "#include <stdio.h>\n"
    '#include "pub/pub_api.h"\n'
    "int main(void) {\n"
    '    printf("%d,%d", pub_inline(), pub_answer());\n'
    "    return 0;\n"
    "}\n"
)


def _run(cmd, cwd, env=None):
    r = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, (cmd, r.stdout[-2500:], r.stderr[-2500:])
    return r


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("gh1599")
    xtra = root / "xtra"
    xtra.mkdir()
    (xtra / "xtra.c").write_text(_XTRA_C)
    _run(["cc", "-fPIC", "-c", "xtra.c", "-o", "xtra.o"], xtra)
    _run(["ar", "rcs", "libxtra.a", "xtra.o"], xtra)

    r = run_cli("new", "pub", "--object", "pub", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = root / "pub"
    cfg = C.load(proj)
    cfg["project"]["public_defines"] = ["_GNU_SOURCE"]
    cfg["project"]["public_link_libs"] = [f"-L{xtra}", "-lxtra"]
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    (proj / "native" / "inc" / "pub" / "pub_api.h").write_text(_API_H)
    core = proj / "native" / "src" / "pub" / "pub_core.c"
    core.write_text(core.read_text() + _CORE_ADD)

    pfx = root / "pfx"
    b = proj / "b"
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            b,
            "-DBUILD_PYTHON=OFF",
            f"-DCMAKE_INSTALL_PREFIX={pfx}",
        ],
        proj,
    )
    # The project's own build: the core compiles only with the definition,
    # and its test and benchmark link only with the flag.
    _run(["cmake", "--build", b], proj)
    _run(["ctest", "--test-dir", b, "--output-on-failure"], proj)
    _run(["cmake", "--install", b], proj)
    (root / "c.c").write_text(_CONSUMER)
    return root, pfx


def _pkg_config(root: Path, pfx: Path, static: bool) -> str:
    env = dict(os.environ, PKG_CONFIG_PATH=str(pfx / "lib" / "pkgconfig"))

    def pc(*args):
        return _run(["pkg-config", *args, "pub"], root, env).stdout.split()

    exe = root / f"pc_{'static' if static else 'shared'}"
    if static:
        libs = [f for f in pc("--static", "--libs") if f != "-lpub"]
        link = [str(pfx / "lib" / "libpub.a"), *libs]
    else:
        link = pc("--libs")
    _run(["cc", *pc("--cflags"), "c.c", *link, "-o", exe], root)
    run_env = dict(os.environ, LD_LIBRARY_PATH=str(pfx / "lib"))
    return _run([exe], root, run_env).stdout


def _find_package(root: Path, pfx: Path, target: str) -> str:
    cons = root / f"fp_{target.replace(':', '_')}"
    cons.mkdir()
    (cons / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(cons C)\n"
        "find_package(pub REQUIRED)\n"
        "add_executable(c ../c.c)\n"
        f"target_link_libraries(c PRIVATE {target})\n"
    )
    _run(["cmake", "-S", ".", "-B", "b", f"-DCMAKE_PREFIX_PATH={pfx}"], cons)
    _run(["cmake", "--build", "b"], cons)
    return _run([cons / "b" / "c"], cons).stdout


@pytest.mark.parametrize("static", [False, True], ids=["shared", "static"])
def test_pkg_config_consumer(installed, static):
    root, pfx = installed
    assert _pkg_config(root, pfx, static) == "42,42"


@pytest.mark.parametrize("target", ["pub::pub", "pub::pub-static"])
def test_find_package_consumer(installed, target):
    root, pfx = installed
    assert _find_package(root, pfx, target) == "42,42"


def test_the_pc_carries_both_publicly(installed):
    _, pfx = installed
    text = (pfx / "lib" / "pkgconfig" / "pub.pc").read_text()
    libs = next(ln for ln in text.splitlines() if ln.startswith("Libs:"))
    cflags = next(ln for ln in text.splitlines() if ln.startswith("Cflags:"))
    assert "-lxtra" in libs, text
    assert "-D_GNU_SOURCE" in cflags, text
    assert (
        "Libs.private" not in text
        or "-lxtra" not in text.split("Libs.private")[1]
    ), text
