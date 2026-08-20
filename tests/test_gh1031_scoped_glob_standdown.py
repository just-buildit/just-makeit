"""gh-1031: a wildcard stands the scan down only where it can REACH.

`_build_texts` returned `None` for the whole tree the moment ANY build file
contained `file(GLOB ...)`. `rglob` reaches `vendor/`, so a vendored
dependency that globs its own sources disabled the scan for everything.

Two consequences, and the second is the worse one:

- gh-1023's `jm bench` discovery delivered NOTHING on doppler — the repo it
  was filed from — because nats.c is vendored and four of its build files
  glob. Same output as the released version it was meant to fix.
- `orphans()` returns `[]` on stand-down, which is indistinguishable from
  "no orphans". So the gh-806 UNBUILT gate had been reporting clean on any
  tree carrying a vendored glob, for as long as it carried one. A gate green
  on nothing.

Excluding `vendor/` by name was the alternative. It is one line and a guess
about naming — `third_party/` and `external/` hit the identical wall. Asking
whether the wildcard can reach is the question the stand-down always claimed
to be asking.

`TestSharesTheScanner` in the gh-1023 suite cannot catch this class: both
readers stand down together, so they partition the same population correctly
while both return nothing.
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

EXTRA_TARGET = """
add_executable(bench_util_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_util_core.c)
"""


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
    (dest / "native" / "benchmarks" / "bench_util_core.c").write_text(
        "int main(void) { return 0; }\n", encoding="utf-8"
    )
    cml = dest / "CMakeLists.txt"
    cml.write_text(
        cml.read_text(encoding="utf-8") + EXTRA_TARGET, encoding="utf-8"
    )
    return dest


def _vendor_glob(project, pattern: str, where: str = "vendor/nats.c/src"):
    d = project / where
    d.mkdir(parents=True, exist_ok=True)
    (d / "CMakeLists.txt").write_text(
        f'file(GLOB SOURCES "{pattern}")\nadd_library(nats ${{SOURCES}})\n',
        encoding="utf-8",
    )


class TestAVendoredGlobIsIrrelevant:
    def test_discovery_still_works(self, project):
        """The repro: `file(GLOB SOURCES "*.c")` in a vendored dependency.

        Its pattern is relative to its own directory and cannot match
        `native/benchmarks/*.c`, so it says nothing about the question asked.
        """
        _vendor_glob(project, "*.c")
        assert _hollow.built_stems(project, "bench") == {"frame", "util"}

    def test_the_orphan_gate_still_reports(self, project):
        """The blast radius beyond bench: `orphans()` returns `[]` when it
        stands down, which reads as a clean tree."""
        _vendor_glob(project, "*.c")
        (project / "native/benchmarks/bench_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        found = _hollow.orphans(project, C.load(project))
        assert [o.stem for o in found] == ["ghost"]

    def test_a_differently_named_vendor_dir_works_too(self, project):
        """What excluding `vendor/` by name would not have covered."""
        _vendor_glob(project, "*.c", where="third_party/foo")
        assert _hollow.built_stems(project, "bench") == {"frame", "util"}


class TestAReachingGlobStillStandsDown:
    """Every "maybe" must still answer stand-down. A false *readable* here
    means jm asserts a target is unbuilt when a wildcard may well build it —
    which sends someone to delete a file that compiles."""

    @pytest.mark.parametrize(
        "pattern",
        [
            "${CMAKE_SOURCE_DIR}/native/benchmarks/*.c",
            "../../../native/benchmarks/*.c",
            "native/benchmarks/*.c",
            "/abs/elsewhere/*.c",
        ],
        ids=["root-var", "walks-up", "names-subdir", "absolute"],
    )
    def test_a_vendored_glob_that_reaches(self, project, pattern):
        _vendor_glob(project, pattern)
        assert _hollow.built_stems(project, "bench") is None

    def test_a_glob_in_the_root_build_file(self, project):
        """The root is an ancestor of everything, so a GLOB_RECURSE there
        could descend into the scanned directory."""
        cml = project / "CMakeLists.txt"
        cml.write_text(
            cml.read_text(encoding="utf-8") + '\nfile(GLOB E "*.c")\n',
            encoding="utf-8",
        )
        assert _hollow.built_stems(project, "bench") is None

    def test_a_glob_in_the_scanned_directory_itself(self, project):
        (project / "native/benchmarks/CMakeLists.txt").write_text(
            'file(GLOB B "*.c")\n', encoding="utf-8"
        )
        assert _hollow.built_stems(project, "bench") is None


class TestStandDownIsPerKind:
    """`test` and `bench` live in different directories, so a wildcard
    reaching one need not disable the other."""

    def test_a_glob_in_native_tests_leaves_bench_readable(self, project):
        (project / "native/tests/CMakeLists.txt").write_text(
            'file(GLOB T "*.c")\n', encoding="utf-8"
        )
        assert _hollow.built_stems(project, "bench") == {"frame", "util"}
        assert _hollow.built_stems(project, "test") is None
