"""gh-1031: jm does not read a vendored dependency's build files.

`_build_texts` returned `None` for the whole tree the moment ANY build file
contained `file(GLOB ...)`, and its `rglob` walks into `vendor/`. So one
vendored dependency globbing its own sources stood the scan down for
everything.

The consequence that matters is not the one in the title of gh-1023.
`orphans()` returns `[]` when it stands down, which is indistinguishable from
a clean tree — so the gh-806 UNBUILT gate had been reporting green on any
project carrying a vendored glob, for as long as it carried one. A gate green
on nothing, inside the detector written to find exactly that.

**jm does not try to decide whether a given wildcard could reach the directory
being scanned.** That was implemented and reverted. It meant reading a foreign
build system's patterns for `..`, an absolute path or a project-root variable
and inferring their extent, and it fails in the expensive direction: a pattern
jm reads as irrelevant that in fact matches makes jm call a compiled file
unbuilt, which sends someone to delete it. A directory jm declines to read at
all cannot be misread.

Stated cost, asserted below so it stays honest: `third_party/` and `external/`
are the same situation under a different name and are still read.
"""

from __future__ import annotations

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _hollow  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", dest)
        object_run(
            dest,
            "frame",
            module=None,
            arg_type="float",
            return_type="float",
            state_vars=[("g", "double", "1.0")],
        )
    return dest


def _glob_at(project, where: str, pattern: str = "*.c"):
    d = project / where
    d.mkdir(parents=True, exist_ok=True)
    (d / "CMakeLists.txt").write_text(
        f'file(GLOB SOURCES "{pattern}")\nadd_library(x ${{SOURCES}})\n',
        encoding="utf-8",
    )


def _orphan(project, stem="ghost"):
    (project / f"native/benchmarks/bench_{stem}_core.c").write_text(
        "int main(void) { return 0; }\n", encoding="utf-8"
    )


class TestVendorIsNotRead:
    def test_the_orphan_gate_still_reports_through_a_vendored_glob(
        self, project
    ):
        """The load-bearing one. Before this, the gate went silent — and
        silent is what a passing gate looks like."""
        _glob_at(project, "vendor/nats.c/src")
        _orphan(project)
        found = _hollow.orphans(project, C.load(project))
        assert [o.stem for o in found] == ["ghost"]

    def test_bench_discovery_still_works(self, project):
        _glob_at(project, "vendor/nats.c/src")
        assert _hollow.built_stems(project, "bench") == {"frame"}

    def test_a_vendored_glob_that_names_the_scanned_path_is_still_ignored(
        self, project
    ):
        """jm reads nothing under `vendor/`, so the PATTERN never matters.

        This is the deliberate simplification: no inference about what a
        foreign build system's wildcard covers. If a vendored build file
        genuinely compiled `native/benchmarks/*.c`, jm would miss it — and
        that trade is taken knowingly, because the alternative was jm
        inferring extent and being wrong in the direction that deletes files.
        """
        _glob_at(project, "vendor/x", "${CMAKE_SOURCE_DIR}/native/**/*.c")
        _orphan(project)
        assert [o.stem for o in _hollow.orphans(project, C.load(project))] == [
            "ghost"
        ]


class TestEverywhereElseStillStandsDown:
    """No inference means the rule stays blunt: a glob jm DOES read stands
    the scan down, wherever in the readable tree it sits."""

    @pytest.mark.parametrize(
        "where", [".", "native/benchmarks", "native/src/frame"]
    )
    def test_a_glob_in_a_read_directory(self, project, where):
        _glob_at(project, where)
        assert _hollow.built_stems(project, "bench") is None
        assert _hollow.orphans(project, C.load(project)) == []

    def test_third_party_is_still_read_and_still_stands_down(self, project):
        """The stated cost of naming one directory instead of inferring.

        Asserted rather than left in prose so the gap is visible when someone
        hits it, and so widening the set is a one-line change with a test
        that already describes the behaviour.
        """
        _glob_at(project, "third_party/foo")
        assert _hollow.built_stems(project, "bench") is None


class TestBuildIsStillSkipped:
    def test_generated_output_is_not_read(self, project):
        _glob_at(project, "build/CMakeFiles")
        assert _hollow.built_stems(project, "bench") == {"frame"}
