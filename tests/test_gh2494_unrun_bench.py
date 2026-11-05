"""gh-2494: a bench target that ``jm bench`` never runs is reported clean.

``jm bench`` runs ``bench_<s>_core`` for each ``s`` in
``_bench.runnable_comps`` and nothing else. doppler moved one benchmark into
a hand-owned ``*_extra.cmake`` as::

    add_executable(
      bench_correlator_1d
      ${CMAKE_SOURCE_DIR}/native/benchmarks/correlator/bench_correlator_1d.c)

and all ten of its rows went missing from ``jm bench`` while ``make bench``
and ``jm status --check`` were both green: the source is compiled, so it is
no gh-806 orphan, and it never runs, so it cannot be a silent benchmark.

Each finding is asserted twice, on ``_hollow.unrun_benches`` and through
``jm status --check``'s exit code, because a finding that never reaches the
gate is the same silence one layer up (the gh-806 rule). The control --
the run set's own ``bench_<s>_core`` targets are NOT reported -- is what
keeps the positive tests from passing on a detector that reports every
target it sees.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _hollow
from just_makeit import _render as R

#: The doppler shape: a second executable over a bench source, its name on
#: the line after ``add_executable(``, and not ``bench_<s>_core``.
_DOPPLER_SHAPE = """\
add_executable(
  bench_o_1d
  ${CMAKE_SOURCE_DIR}/native/benchmarks/o/bench_o_1d.c)
target_link_libraries(bench_o_1d PRIVATE o_core ${JM_MATH_LIBRARY})
"""

#: The allow key doppler's shape is exempted by: `<build file>:<target>`.
_KEY = "native/src/o/" + R.extra_cmake_name("o") + ":bench_o_1d"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A one-object project, clean, with the doppler-shaped source on disk."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    r = run_cli("object", "o", cwd=root)
    assert r.returncode == 0, r.stderr
    bench = root / "native" / "benchmarks"
    (bench / "o").mkdir()
    (bench / "o" / "bench_o_1d.c").write_text(
        (bench / "bench_o_core.c").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return root


def _hook(root: Path) -> Path:
    return root / "native" / "src" / "o" / R.extra_cmake_name("o")


def _allow(root: Path, *patterns: str) -> None:
    manifest = root / C.FILENAME
    listed = ", ".join(f'"{p}"' for p in patterns)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "[project]", f"[project]\nstatus_allow = [{listed}]", 1
        ),
        encoding="utf-8",
    )


def test_a_fresh_project_has_no_unrun_bench(project: Path) -> None:
    """The control: jm's own ``bench_o_core`` is in the run set."""
    assert _hollow.unrun_benches(project, C.load(project)) == []
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNRUN" not in r.stdout


def test_the_doppler_shape_is_reported_and_gates(project: Path) -> None:
    _hook(project).write_text(_DOPPLER_SHAPE, encoding="utf-8")

    found = _hollow.unrun_benches(project, C.load(project))
    assert [u.key for u in found or []] == [_KEY]

    r = run_cli("status", "--check", cwd=project)
    assert r.returncode != 0, r.stdout
    assert "UNRUN (1)" in r.stdout
    assert f"! {_KEY}" in r.stdout
    assert "1 unrun (!)" in r.stdout

    j = run_cli("status", "--check", "--json", cwd=project)
    assert j.returncode != 0
    doc = json.loads(j.stdout)
    assert doc["unrun_benches"] == [
        {
            "path": "native/src/o/" + R.extra_cmake_name("o"),
            "target": "bench_o_1d",
            "key": _KEY,
            "allowed": False,
        }
    ]


def test_a_bench_source_under_another_name_is_reported(
    project: Path,
) -> None:
    """The issue's own rule: a target over a bench-tree source, any name."""
    _hook(project).write_text(
        "add_executable(o_perf\n"
        "  ${CMAKE_SOURCE_DIR}/native/benchmarks/o/bench_o_1d.c)\n",
        encoding="utf-8",
    )
    found = _hollow.unrun_benches(project, C.load(project))
    assert [u.target for u in found or []] == ["o_perf"]


def test_a_non_bench_executable_is_not_reported(project: Path) -> None:
    """Neither ``bench_``-named nor over a bench source: not a benchmark."""
    _hook(project).write_text(
        "add_executable(o_tool ${CMAKE_SOURCE_DIR}/native/src/o/tool.c)\n",
        encoding="utf-8",
    )
    assert _hollow.unrun_benches(project, C.load(project)) == []


def test_a_correctly_named_target_is_run_and_not_reported(
    project: Path,
) -> None:
    """``bench_<s>_core`` over ``bench_<s>_core.c`` is one jm bench runs."""
    bench = project / "native" / "benchmarks"
    (bench / "bench_o_hand_core.c").write_text(
        (bench / "bench_o_core.c").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _hook(project).write_text(
        "add_executable(\n"
        "  bench_o_hand_core\n"
        "  ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_o_hand_core.c)\n",
        encoding="utf-8",
    )
    assert _hollow.unrun_benches(project, C.load(project)) == []
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr


def test_status_allow_exempts_the_one_target(project: Path) -> None:
    _hook(project).write_text(_DOPPLER_SHAPE, encoding="utf-8")
    _allow(project, _KEY)

    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    # Exempt from the gate is not invisible, and not "in sync".
    assert f"~ {_KEY} [status_allow]" in r.stdout
    assert "1 unrun (allowed)" in r.stdout
    # A finding key matches no managed file; it must not read as stale.
    # The STALE ALLOW listing is printed only without --check, so ask there.
    plain = run_cli("status", cwd=project)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "STALE ALLOW" not in plain.stdout, plain.stdout


def test_a_glob_allow_that_suppresses_is_not_stale(project: Path) -> None:
    """A glob is honoured by the finding, so it is not "stale" either.

    `_is_allowed` matches by fnmatch; reading staleness by exact key
    suppressed the target and called the same pattern stale in one run.
    """
    _hook(project).write_text(_DOPPLER_SHAPE, encoding="utf-8")
    _allow(project, "native/src/o/*:bench_*")

    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"~ {_KEY} [status_allow]" in r.stdout
    plain = run_cli("status", cwd=project)
    assert "STALE ALLOW" not in plain.stdout, plain.stdout


def test_a_wildcard_build_file_stands_the_scan_down(project: Path) -> None:
    """``None``, never ``[]``: the scan did not run (gh-1033)."""
    _hook(project).write_text(
        _DOPPLER_SHAPE + "file(GLOB _extra native/benchmarks/o/*.c)\n",
        encoding="utf-8",
    )
    assert _hollow.unrun_benches(project, C.load(project)) is None


@pytest.mark.parametrize(
    "where",
    [
        # A nested project, benchmarked by its own `jm bench`.
        pytest.param("examples/downstream", id="nested-project"),
        # An installed jm's bundled example tree, or a second checkout.
        pytest.param(".venv/lib/site/tree", id="hidden-dir"),
    ],
)
def test_a_build_file_that_is_not_this_projects_is_not_read(
    project: Path, where: str
) -> None:
    """Measured on doppler: both shapes, unscoped, gated on ~90 targets.

    The control is the same target in this project's own hook, which IS
    reported -- so a scan that reads nothing cannot pass this test.
    """
    other = project / where
    other.mkdir(parents=True)
    if where.startswith("examples"):
        (other / C.FILENAME).write_text(
            '[project]\nname = "d"\n', encoding="utf-8"
        )
    (other / "CMakeLists.txt").write_text(_DOPPLER_SHAPE, encoding="utf-8")
    assert _hollow.unrun_benches(project, C.load(project)) == []

    _hook(project).write_text(_DOPPLER_SHAPE, encoding="utf-8")
    found = _hollow.unrun_benches(project, C.load(project))
    assert [u.key for u in found or []] == [_KEY]
