"""gh-1473: a build tree is not the project, whatever it is called.

`_apply._SKIP_DIRS` knew the NAME ``build`` and nothing else, so any other
CMake binary directory inside a project was copied into `status`'s scratch
and counted as manifest-owned: 35 files became 116 with one
``cmake -B build-rel``, and CLion's default ``cmake-build-debug`` is the
same. The count belonged to the toolchain, not to jm. (``out/build/<preset>``,
where jm's own presets build, was covered only because one of its components
happens to be spelled ``build`` -- the name rule matched by coincidence.)

The same name-only rule stood in `_hollow`: a build tree holds FetchContent's
``_deps/*-src``, somebody else's build system, and one ``file(GLOB`` there
stood the unbuilt-source scan down for the whole project -- gh-1031's
``vendor/`` failure under a different name.

Both are recognised now by what a build tree IS -- CMake writes
``CMakeCache.txt`` at its top -- and these tests make one the way CMake
would, under names in which no path component is ``build`` -- a fixture
under ``out/build`` passed with the fix sabotaged, which is how that
coincidence was found.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _hollow


def _fake_build_tree(root: Path, rel: str) -> None:
    """What `cmake -B <rel>` leaves, minus the compile: the cache, some
    object files and headers, and a fetched dependency whose CMakeLists
    globs its own sources."""
    b = root / rel
    (b / "CMakeFiles" / "gain.dir").mkdir(parents=True)
    (b / "CMakeCache.txt").write_text("CMAKE_BUILD_TYPE:STRING=Release\n")
    for i in range(20):
        (b / "CMakeFiles" / "gain.dir" / f"f{i}.c.o").write_bytes(b"\0")
    (b / "generated.h").write_text("#define X 1\n")
    dep = b / "_deps" / "foo-src"
    dep.mkdir(parents=True)
    (dep / "CMakeLists.txt").write_text(
        'file(GLOB SRCS "*.c")\nadd_library(foo ${SRCS})\n'
    )


def _owned_count(out: str) -> int:
    m = re.search(r"(\d+) manifest-owned file\(s\) match", out)
    assert m, out
    return int(m.group(1))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    r = run_cli(
        "object",
        "gain",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    return root


@pytest.mark.parametrize("rel", ["cmake-build-debug", "build-rel"])
def test_status_does_not_count_a_build_tree(project: Path, rel: str) -> None:
    before = run_cli("status", "--check", cwd=project)
    assert before.returncode == 0, before.stdout + before.stderr
    n = _owned_count(before.stdout)

    _fake_build_tree(project, rel)

    after = run_cli("status", "--check", cwd=project)
    assert after.returncode == 0, after.stdout + after.stderr
    assert _owned_count(after.stdout) == n, after.stdout
    assert rel not in after.stdout


def test_hollow_scan_ignores_a_globbing_dep_in_a_build_tree(
    project: Path,
) -> None:
    cfg = C.load(project)
    assert _hollow.orphans(project, cfg) is not None
    _fake_build_tree(project, "cmake-build-debug")
    # A stand-down (None) would mean the fetched dep's glob was read as the
    # project's own, and the scan covers nothing.
    assert _hollow.orphans(project, cfg) is not None
