"""The combined C library's two flavours must not share a file on Windows.

gh-1368. The shared and static ``lib<pkg>`` targets share one OUTPUT_NAME,
which is unambiguous on Linux and macOS (``lib<pkg>.so`` / ``lib<pkg>.a``).
On Windows the SHARED library's import library and the STATIC library are
both ``<pkg>.lib``, and Ninja refuses the project before any C compiles:
``multiple rules generate <pkg>.lib``. It failed 22 of 25 examples on the
first clang-cl run.

Checked by CMake itself, not by reading the template: the project's own
library block runs under ``cmake -P`` with ``WIN32`` set either way, and the
test reads back the OUTPUT_NAME each target ends up with.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._new import run as new_run  # noqa: E402


def _names(root: Path, win32: bool) -> "tuple[str, str]":
    """OUTPUT_NAME of the shared and static library, as CMake evaluates the
    project's own library block with WIN32 set or not."""
    top = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    start = top.index("add_library(p_lib SHARED")
    end = top.index("enable_testing()")
    block = top[start:end]
    script = root / "probe.cmake"
    # Real targets need a project; a script can't define them. So stand in
    # for the two calls CMake would make and record what the block asks for.
    script.write_text(
        f"set(WIN32 {'1' if win32 else '0'})\n"
        "macro(add_library name)\n  set(_out_${name} ${name})\nendmacro()\n"
        "macro(target_include_directories)\nendmacro()\n"
        "function(set_target_properties t)\n"
        '  cmake_parse_arguments(P "" "OUTPUT_NAME" "" ${ARGN})\n'
        "  set(_out_${t} ${P_OUTPUT_NAME} PARENT_SCOPE)\nendfunction()\n"
        + block
        + 'message("${_out_p_lib}|${_out_p_lib_static}")\n',
        encoding="utf-8",
    )
    r = subprocess.run(
        ["cmake", "-P", str(script)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    shared, static = r.stderr.strip().splitlines()[-1].split("|")
    return shared, static


@pytest.mark.skipif(not shutil.which("cmake"), reason="needs cmake")
@pytest.mark.parametrize("win32", [True, False], ids=["windows", "posix"])
def test_the_two_libraries_get_distinct_files(tmp_path, win32):
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root)
    shared, static = _names(root, win32)
    assert shared == "p"
    # POSIX keeps one name on purpose: libp.so / libp.a already differ, and
    # renaming there would change what every existing consumer links.
    assert static == ("p_static" if win32 else "p")
