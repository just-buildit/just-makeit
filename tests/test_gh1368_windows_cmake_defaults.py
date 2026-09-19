"""The generated root CMake's Windows defaults (gh-1368).

Three things the second clang-cl run needed from the root ``CMakeLists.txt``:

- an unset build type is **Release**. CMake's own default for an MSVC-like
  compiler is Debug, which defines ``_DEBUG``, which makes ``pyconfig.h`` link
  ``python3X_d.lib``, a library only a debug Python ships;
- the CRT's deprecation noise is silenced and ``M_PI`` exposed under WIN32;
- clang-cl inlines complex multiply/divide (``/clang:-fcx-limited-range``),
  or the link needs ``__mulsc3`` and nothing in an MSVC link defines it.

The first is measured by configuring a real generated project, which works
on any host. The other two can't be configured off Windows, so the project's
own blocks are run under ``cmake -P`` with the platform variables set, and
the test reads what they ask for.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._new import run as new_run  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("cmake") and shutil.which("cc")),
    reason="needs cmake and a C compiler",
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root)
    return root


def _cached_build_type(root: Path, *extra: str) -> str:
    build = root / "build"
    r = subprocess.run(
        [
            "cmake",
            "-B",
            str(build),
            "-S",
            str(root),
            "-DBUILD_PYTHON=OFF",
            *extra,
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    cache = (build / "CMakeCache.txt").read_text(encoding="utf-8")
    return re.search(r"^CMAKE_BUILD_TYPE:\w+=(.*)$", cache, re.M).group(1)


def test_an_unset_build_type_is_release(tmp_path):
    assert _cached_build_type(_project(tmp_path)) == "Release"


@pytest.mark.parametrize("given", ["Debug", ""])
def test_an_explicit_build_type_is_kept(tmp_path, given):
    """Including an explicit empty one: `-DCMAKE_BUILD_TYPE=` is a choice."""
    root = _project(tmp_path)
    assert _cached_build_type(root, f"-DCMAKE_BUILD_TYPE={given}") == given


def _platform_asks(root: Path, *, win32: bool, msvc: bool, cid: str):
    """(definitions, options) the root's platform blocks add, as CMake reads
    them with these platform variables."""
    top = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    start = top.index("if(WIN32)\n  # The CRT")
    end = top.index('option(BUILD_PYTHON "Build Python C extensions" ON)')
    script = root / "probe.cmake"
    script.write_text(
        f"set(WIN32 {int(win32)})\nset(MSVC {int(msvc)})\n"
        f'set(CMAKE_C_COMPILER_ID "{cid}")\n'
        "set(_defs)\nset(_opts)\n"
        "macro(add_compile_definitions)\n  list(APPEND _defs ${ARGN})\nendmacro()\n"
        "macro(add_compile_options)\n  list(APPEND _opts ${ARGN})\nendmacro()\n"
        + top[start:end]
        + 'message("${_defs}|${_opts}")\n',
        encoding="utf-8",
    )
    r = subprocess.run(
        ["cmake", "-P", str(script)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    defs, opts = r.stderr.strip().splitlines()[-1].split("|")
    return set(filter(None, defs.split(";"))), set(
        filter(None, opts.split(";"))
    )


@pytest.mark.parametrize(
    "win32, msvc, cid, defs, opts",
    [
        (
            True,
            True,
            "Clang",
            {
                "_CRT_SECURE_NO_WARNINGS",
                "_CRT_NONSTDC_NO_DEPRECATE",
                "_USE_MATH_DEFINES",
            },
            {"/clang:-fcx-limited-range"},
        ),
        # cl.exe would reject /clang:; it cannot build _Complex anyway.
        (
            True,
            True,
            "MSVC",
            {
                "_CRT_SECURE_NO_WARNINGS",
                "_CRT_NONSTDC_NO_DEPRECATE",
                "_USE_MATH_DEFINES",
            },
            set(),
        ),
        # MinGW: Windows, not MSVC.
        (
            True,
            False,
            "GNU",
            {
                "_CRT_SECURE_NO_WARNINGS",
                "_CRT_NONSTDC_NO_DEPRECATE",
                "_USE_MATH_DEFINES",
            },
            set(),
        ),
        (False, False, "GNU", set(), set()),
    ],
    ids=["clang-cl", "cl", "mingw", "posix"],
)
def test_the_platform_blocks(tmp_path, win32, msvc, cid, defs, opts):
    got = _platform_asks(_project(tmp_path), win32=win32, msvc=msvc, cid=cid)
    assert got == (defs, opts)
