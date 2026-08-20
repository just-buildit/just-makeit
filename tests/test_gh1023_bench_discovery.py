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


class TestDiscoveryIsAHeuristic:
    """Reading build files as text can say "built" about a target that is not.

    That asymmetry is the whole reason `built_stems` needs care that `orphans`
    did not. For `orphans` a false "built" is a MISSED finding — silent and
    harmless. For `jm bench` it means building a target that does not exist,
    and `_build_bench_target` exits the process, so one bad guess would take
    down a run whose declared components were all fine.
    """

    def test_a_similarly_named_target_does_not_count(self, project):
        """`bench_util_core` is a substring of `bench_util_core_simd`.

        The whole build block is swapped, not just the target name: the
        reference test deliberately matches the SOURCE PATH too (CMake names
        the file, a generated Makefile names the executable), so leaving
        `bench_util_core.c` in an `add_executable` would match either way and
        the test would prove nothing.
        """
        cml = project / "CMakeLists.txt"
        cml.write_text(
            cml.read_text(encoding="utf-8").replace(
                EXTRA_TARGET,
                "\nadd_executable(bench_util_core_simd\n"
                "    ${CMAKE_SOURCE_DIR}/native/benchmarks/"
                "bench_util_core_simd.c)\n",
            ),
            encoding="utf-8",
        )
        # bench_util_core.c is still on disk; nothing names it as a whole word.
        assert (project / "native/benchmarks/bench_util_core.c").exists()
        assert "util" not in _hollow.built_stems(project, "bench")

    def test_a_commented_out_target_still_counts(self, project):
        """Documented, not asserted as desirable.

        The reference test reads text; a comment naming the target is
        indistinguishable from a live one without parsing CMake. This is why
        a DISCOVERED name's build failure is a skip rather than a fatal —
        the guess is allowed to be wrong, it is just not allowed to be
        expensive. See TestDiscoveredFailureIsNotFatal.
        """
        cml = project / "CMakeLists.txt"
        cml.write_text(
            cml.read_text(encoding="utf-8").replace(
                "add_executable(bench_util_core",
                "# add_executable(bench_util_core",
            ),
            encoding="utf-8",
        )
        assert "util" in _hollow.built_stems(project, "bench")


class TestDiscoveredFailureIsNotFatal:
    """A guess may be wrong; it may not abort the run."""

    def test_a_discovered_target_that_fails_to_build_is_skipped(
        self, project, monkeypatch, capsys
    ):
        import just_makeit._bench as B

        monkeypatch.setattr(B, "_require", lambda _tool: "/bin/false")
        built = B._build_bench_target(
            project, project / "build", "util", fatal=False
        )
        assert built is False
        assert "skip" in capsys.readouterr().err

    def test_a_manifest_component_still_exits(self, project, monkeypatch):
        """Unchanged: a declared component's target not building is a real
        breakage, not a bad guess."""
        import just_makeit._bench as B

        monkeypatch.setattr(B, "_require", lambda _tool: "/bin/false")
        with pytest.raises(SystemExit):
            B._build_bench_target(
                project, project / "build", "frame", fatal=True
            )

    def test_collect_c_exits_for_a_name_not_in_optional(
        self, project, monkeypatch
    ):
        """The decision under test is `_collect_c`'s, not the builder's.

        Asserting `_build_bench_target(fatal=True)` exits proves the builder
        honours its flag and says nothing about who passes what — marking
        EVERY name optional would swallow a component's real breakage and
        still pass such a test.
        """
        import just_makeit._bench as B

        monkeypatch.setattr(B, "_require", lambda _tool: "/bin/false")
        with pytest.raises(SystemExit):
            B._collect_c(project, project / "build", ["frame"])

    def test_collect_c_skips_a_name_in_optional(self, project, monkeypatch):
        import just_makeit._bench as B

        monkeypatch.setattr(B, "_require", lambda _tool: "/bin/false")
        assert (
            B._collect_c(
                project,
                project / "build",
                ["util"],
                optional=frozenset({"util"}),
            )
            is None
        )

    def test_an_explicitly_named_bench_is_not_optional(self, project):
        """Review finding: `jm bench --check util` could pass on nothing.

        `optional` exists so a heuristic cannot abort a run nobody asked it to
        affect. A name the user typed is not a heuristic — and
        `_compare_reports` yields one record per CURRENT benchmark, so a
        skipped one does not appear as a failure, it simply vanishes from the
        comparison and the gate exits green over less than it claims.
        """
        import just_makeit._bench as B
        import just_makeit._config as CC

        seen = {}

        def _fake_collect(root, bdir, comps, optional=frozenset()):
            seen["optional"] = optional
            return None

        monkey = pytest.MonkeyPatch()
        monkey.setattr(B, "_collect_c", _fake_collect)
        monkey.setattr(B, "_ensure_built", lambda *a, **k: None)
        monkey.setattr(B, "_project_python", lambda _r: "python3")
        try:
            B.run(project, components=["util"], do_python=False, tag="x")
            assert seen["optional"] == frozenset()
            seen.clear()
            B.run(project, do_python=False, tag="y")
            assert seen["optional"] == frozenset({"util"})
        finally:
            monkey.undo()
        assert CC  # keep the import meaningful for the fixture's manifest

    def test_the_reference_test_docstring_is_raw(self):
        """`\\b` in a non-raw docstring compiles to a backspace (0x08)."""
        assert chr(8) not in (_hollow._is_built.__doc__ or "")

    def test_run_marks_only_discovered_names_optional(self, project):
        """The `optional` set must be `extra`, not everything — passing all
        of `runnable` would silently swallow a component's real breakage."""
        runnable, extra, _ = runnable_comps(project, C.load(project))
        assert extra == ["util"]
        assert "frame" in runnable and "frame" not in extra


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
