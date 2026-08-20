"""gh-1023: `jm bench` runs what the tree BUILDS, not what the manifest declares.

`C.components(cfg)` is the manifest's top-level tables — the objects — and it
was both the enumeration and the validator's whitelist. So a benchmark for
anything else could be written, reviewed, registered as a CMake target,
compiled by every build since, and executed by nothing, while
`jm bench <that name>` answered `unknown component`. Unreachable twice over,
and silent in both directions: the file exists, the target builds,
`jm status --check` is clean (a benchmark is not manifest-owned), and a
snapshot that omits a component looks exactly like one that includes it.
Auditing doppler before a release turned up four in that state.

The concrete shape is a `[project] c_deps` directory, whose `CMakeLists.txt`
is hand-written — jm emits only the `add_subdirectory` line, so a bench target
inside it genuinely builds and is genuinely undiscoverable.

Discovery is by SCAN rather than by a new manifest key. `_hollow.built_stems`
is the complement of `_hollow.orphans`, sharing its scanner, its `_KINDS`
shapes and its stem-substring reference test — see TestSharesTheScanner,
which is what keeps the pair from disagreeing about what "built" means.
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
from just_makeit._bench import runnable_comps  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

EXTRA_TARGET = """
add_executable(bench_util_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_util_core.c)
"""


@pytest.fixture()
def project(tmp_path):
    """One object plus a hand-registered, non-component benchmark."""
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


class TestDiscovery:
    def test_finds_a_benchmark_the_manifest_never_mentions(self, project):
        assert "util" not in C.components(C.load(project))
        assert _hollow.built_stems(project, "bench") == {"frame", "util"}

    def test_an_unbuilt_source_is_not_discovered(self, project):
        """The complement half: a file nothing compiles must NOT be run.

        `jm bench` would otherwise try to build a target that does not exist
        and fail on a file the author has not wired up yet.
        """
        (project / "native" / "benchmarks" / "bench_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        built = _hollow.built_stems(project, "bench")
        assert "ghost" not in built
        assert "util" in built

    def test_a_globbing_build_stands_down_distinguishably(self, project):
        """`None`, not an empty set.

        "I could not tell" and "there is nothing extra" lead to the same run
        set but not to the same OUTPUT — the caller says so on the first and
        stays quiet on the second, because running fewer benchmarks than the
        tree holds is the bug being fixed.
        """
        cml = project / "CMakeLists.txt"
        cml.write_text(
            cml.read_text(encoding="utf-8") + '\nfile(GLOB _s "*.c")\n',
            encoding="utf-8",
        )
        assert _hollow.built_stems(project, "bench") is None

    def test_no_benchmarks_directory_is_empty_not_none(self, tmp_path):
        dest = tmp_path / "bare"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("bare", dest)
        assert _hollow.built_stems(dest, "bench") == set()

    def test_unknown_kind_is_refused(self, project):
        with pytest.raises(ValueError):
            _hollow.built_stems(project, "nonsense")


class TestSharesTheScanner:
    """`built_stems` and `orphans` must partition the same population.

    Two scanners would drift, and the drift is invisible: a source counted as
    built by one and orphaned by the other is either run twice or reported as
    dead while it runs.
    """

    def test_built_and_orphaned_are_disjoint(self, project):
        cfg = C.load(project)
        (project / "native" / "benchmarks" / "bench_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        built = _hollow.built_stems(project, "bench")
        orphaned = {
            o.stem for o in _hollow.orphans(project, cfg) if o.kind == "bench"
        }
        assert built & orphaned == set()
        assert "ghost" in orphaned

    def test_the_existing_gate_is_silent_on_this_bug(self, project):
        """Why gh-1023 needed a new reader rather than a louder `orphans`.

        doppler's four benchmarks were never reported by the gh-806 hollow
        gate because their targets DO build — `orphans` finds sources nothing
        compiles, which is the opposite population.
        """
        assert [o for o in _hollow.orphans(project, C.load(project))] == []


class TestRunSet:
    """The enumeration and the validator's whitelist are one set."""

    def _runnable(self, root):
        # The helper `jm bench` itself calls. A second copy of this
        # expression here would assert on the copy, not on the code — and
        # "the run set and the accepted set are one expression" is the whole
        # point of the helper.
        return runnable_comps(root, C.load(root))[0]

    def test_a_discovered_name_is_selectable(self, project):
        assert "util" in self._runnable(project)

    def test_a_genuinely_unknown_name_is_still_rejected(self, project):
        assert "nope" not in self._runnable(project)

    def test_components_are_never_dropped(self, project):
        """Widening must not narrow: every manifest component stays runnable
        even if its bench source is missing entirely."""
        (project / "native" / "benchmarks" / "bench_frame_core.c").unlink()
        assert "frame" in self._runnable(project)
