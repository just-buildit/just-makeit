"""Every header jm generates compiles cleanly into a TU that calls nothing.

gh-2497. ``jm_bench.h`` defined ``jm_bench_write_json`` (and two helpers)
as plain ``static``, so a bench translation unit that included the header
without calling it failed ``-Wunused-function`` under ``-Wall -Wextra
-Werror`` -- doppler split one bench binary's rows across two ``.c`` files
and hit it. A header-defined function must be ``static inline``.

Registration-free: the headers are whatever a scaffolded project carries
under ``native/inc`` -- a standalone object and a module object, rendered by
jm the way a real project gets them -- not a hand list, so a new header is
covered the day jm starts writing it. Each is compiled, alone, into an
object file, as ``-std=gnu99`` (``CMAKE_C_STANDARD 99`` with CMake's
default extensions), by every C compiler on the box (gcc and clang); a missing one
is a skip naming it, and a skip is not a pass.
"""

from __future__ import annotations

from pathlib import Path

import sys

import pytest

import _cbuild as cbuild
from _compilers import find_compiler
from _jmrun import run_cli

#: The compilers a header must be warning-clean under. Windows has no gcc
#: leg (MinGW is retired, gh-1368): there it is the compiler a Windows
#: project builds with, clang-cl, and plain clang (gh-2294).
COMPILERS = (
    ("clang-cl", "clang") if sys.platform == "win32" else ("gcc", "clang")
)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project with a standalone object and a module object."""
    root = tmp_path_factory.mktemp("gh2497")
    assert run_cli("new", "hdrs", "--perf", cwd=root).returncode == 0
    proj = root / "hdrs"
    assert run_cli("object", "eng", cwd=proj).returncode == 0
    assert run_cli("module", "filt", cwd=proj).returncode == 0
    r = run_cli("object", "fir", "--module", "filt", cwd=proj)
    assert r.returncode == 0, r.stderr
    return proj


def _headers(proj: Path) -> "list[Path]":
    return sorted((proj / "native").rglob("*.h"))


def test_the_tree_carries_the_shared_headers(project: Path) -> None:
    """The sweep is armed: the headers gh-2497 is about are in it."""
    names = {h.name for h in _headers(project)}
    shared = {
        "jm_bench.h",
        "jm_test.h",
        "jm_perf.h",
        "jm_simd.h",
        "clib_common.h",
        "pyex_common.h",
    }
    assert shared <= names, names
    # Every header template renders to at least one file here.
    templates = Path(__file__).parents[1] / "src/just_makeit/templates/c/inc"
    assert len(names) >= len(list(templates.glob("*.h"))), names


@pytest.mark.parametrize("cc_name", COMPILERS)
def test_each_header_compiles_with_nothing_called(
    project: Path, cc_name: str, tmp_path: Path
) -> None:
    cc = find_compiler(cc_name)
    if cc is None:
        pytest.skip(f"{cc_name} is not installed; this leg did not run")
    inc = project / "native" / "inc"
    pytest.importorskip("numpy")
    failures = []
    for hdr in _headers(project):
        tu = tmp_path / "tu.c"
        rel = hdr.relative_to(project).as_posix()
        tu.write_text(f'#include "{hdr.name}"\n')
        r = cbuild.build(
            "obj",
            [tu],
            tmp_path / ("tu" + cbuild.suffix("obj", cc)),
            compiler=cc,
            check=False,
            std="gnu99",
            warnings=["all", "extra", "error"],
            includes=[hdr.parent, inc],
            python=True,
            numpy=True,
        )
        if r.returncode != 0:
            failures.append(f"{rel}:\n{r.stderr}")
    assert not failures, "\n".join(failures)
