"""gh-1432 item 4: a target wired in ``<dir>_extra.cmake`` is built.

Every CMakeLists jm renders under ``native/src/<dir>/`` ends by including
``<dir>_extra.cmake`` beside it (gh-1351), and that hook is where jm itself
tells an author to put hand CMake. The gh-806 unbuilt-source scan read the
CMakeLists files and the Makefiles but never the hook, so a benchmark wired
there -- doppler's ``bench_buffer_core.c`` -- was reported as compiled by no
build file, and ``jm status --check`` went red on correct work.

The fixture's hook is not a string that merely mentions the target: it is
the CMake that was measured to configure and build the benchmark, so the
"built" answer asserted here is the true one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _hollow
from just_makeit import _render as R

#: A hand benchmark, wired the way an author would: in the component's hook.
_HOOK = """\
add_executable(bench_o_hand_core
    ${PROJECT_SOURCE_DIR}/native/benchmarks/bench_o_hand_core.c)
target_link_libraries(bench_o_hand_core PRIVATE o_core ${JM_MATH_LIBRARY})
target_include_directories(bench_o_hand_core
    PRIVATE ${PROJECT_SOURCE_DIR}/native/benchmarks)
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A one-object project with a second, hand-written benchmark source."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    r = run_cli("object", "o", cwd=root)
    assert r.returncode == 0, r.stderr
    bench = root / "native" / "benchmarks"
    (bench / "bench_o_hand_core.c").write_text(
        (bench / "bench_o_core.c").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return root


def _hook(root: Path) -> Path:
    return root / "native" / "src" / "o" / R.extra_cmake_name("o")


def test_unwired_source_is_still_reported(project: Path) -> None:
    """The control: with no hook, the file IS unbuilt, and says so.

    Without this the test below could pass on a scan that reports nothing
    at all.
    """
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode != 0, r.stdout
    assert "bench_o_hand_core.c" in r.stdout
    found = _hollow.orphans(project, C.load(project))
    assert [o.rel for o in found or []] == [
        "native/benchmarks/bench_o_hand_core.c"
    ]


def test_a_target_wired_in_the_hook_is_built(project: Path) -> None:
    _hook(project).write_text(_HOOK, encoding="utf-8")

    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "bench_o_hand_core" not in r.stdout
    assert _hollow.orphans(project, C.load(project)) == []
    # The same scan is `jm bench`'s discovery (gh-1023): a target the hook
    # builds is one `jm bench` can run.
    assert _hollow.built_stems(project, "bench") == {"o", "o_hand"}


def test_a_wildcard_in_the_hook_stands_the_scan_down(project: Path) -> None:
    """The hook is read as a build file like any other, so a ``file(GLOB``
    in it makes "is this compiled?" unanswerable -- the stand-down, not a
    guess in either direction."""
    _hook(project).write_text(
        'file(GLOB HAND "${PROJECT_SOURCE_DIR}/native/benchmarks/*.c")\n',
        encoding="utf-8",
    )
    assert _hollow.orphans(project, C.load(project)) is None
