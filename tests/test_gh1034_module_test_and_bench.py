"""gh-1034: a function-only module gets the C test and benchmark an object has.

jm generates and owns a module whose surface is free functions — its `_ext.c`,
its `.pyi`, its `CMakeLists.txt` — and generated no C test for it, no
benchmark, and no target for either. `CMakeLists_module.cmake` had zero
references to both. So the one component whose C jm writes end to end was the
one with nothing checking it, and every consumer hand-registered both in its
root CMakeLists: doppler ended up with five such registrations.

Two of doppler's component-name defects (a benchmark writing a JSON filename
nothing opens) exist precisely because nothing GENERATED those targets, so
nothing generated the string either. A generated target cannot get its own
component name wrong — TestNamesAreGenerated pins that.

The scope is scaffold + target + run. Not a timing loop: only a human knows
what question a benchmark asks. That makes the generated benchmark silent from
birth, which is why TestTheDetectorSeesIt matters — the gh-806 SILENT detector
walked `C.components(cfg)`, i.e. objects only, so the detector written to find
exactly this shape was blind to the one file jm now creates already in it.
"""

from __future__ import annotations

import io
import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _hollow  # noqa: E402
from just_makeit import _render as R  # noqa: E402
from just_makeit._bench import runnable_comps  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._function import run as fn_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", dest, modules=["util"])
        fn_run(
            dest,
            "ema_step",
            module="util",
            return_type="double",
            params=[("x", "double"), ("a", "double")],
        )
    return dest


def _test_c(p):
    return p / "native" / "tests" / "test_util_core.c"


def _bench_c(p):
    return p / "native" / "benchmarks" / "bench_util_core.c"


def _module_cmake(p):
    return (p / "native" / "src" / "util" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )


class TestScaffold:
    def test_both_sources_exist(self, project):
        assert _test_c(project).is_file()
        assert _bench_c(project).is_file()

    def test_both_targets_are_registered(self, project):
        cmake = _module_cmake(project)
        assert "add_executable(test_util_core" in cmake
        assert "add_test(NAME test_util_core" in cmake
        assert "add_executable(bench_util_core" in cmake

    def test_the_shared_harnesses_are_written(self, project):
        """A quoted include resolves against the including file's directory,
        which is how an object's test finds `jm_test.h`. A function-only
        project had nothing that wrote either harness, so the generated test
        did not compile until this did."""
        assert (project / "native/tests/jm_test.h").is_file()
        assert (project / "native/benchmarks/jm_bench.h").is_file()

    def test_the_smoke_test_calls_the_function(self, project):
        body = _test_c(project).read_text(encoding="utf-8")
        assert "(void)ema_step(0.0, 0.0);" in body

    def test_the_benchmark_carries_the_worked_example(self, project):
        """The TODO block is the one in `_methods`, not a second copy — its
        `jm_bench_add` example carries its own `elapsed_sec` so pasting it
        compiles."""
        body = _bench_c(project).read_text(encoding="utf-8")
        assert "TODO" in body
        assert "jm_bench_add(&_bench" in body
        assert "elapsed_sec" in body
        assert "ema_step(...)" in body


class TestNamesAreGenerated:
    def test_the_json_component_name_matches_the_target(self, project):
        """doppler shipped `"hbdecim_core"` where `"hbdecim"` was meant, so
        the file it wrote was one nothing opened. Both strings come from one
        variable here."""
        body = _bench_c(project).read_text(encoding="utf-8")
        assert 'jm_bench_write_json(&_bench, "util");' in body
        assert "bench_util_core" in _module_cmake(project)


class TestRunSet:
    def test_the_module_is_runnable_by_declaration(self, project):
        """Not via the gh-1023 scan. jm generates this target, so it belongs
        in the run set by declaration — leaning on discovery would couple a
        thing jm generates to the discovery of things it does not."""
        runnable, extra, _ = runnable_comps(project, C.load(project))
        assert "util" in runnable
        assert extra == []


class TestTheDetectorSeesIt:
    def test_a_scaffolded_module_bench_is_reported_silent(self, project):
        silent = _hollow.silent_benches(project, C.load(project))
        assert [s.component for s in silent] == ["util"]
        assert silent[0].functions == 1

    def test_it_stops_being_silent_once_a_measurement_is_added(self, project):
        src = _bench_c(project)
        src.write_text(
            src.read_text(encoding="utf-8").replace(
                "    jm_bench_write_json",
                '    jm_bench_add(&_bench, "ema", times, 1, 1);\n'
                "    jm_bench_write_json",
            ),
            encoding="utf-8",
        )
        assert _hollow.silent_benches(project, C.load(project)) == []

    def test_the_detail_names_functions_not_a_missing_step(self, project):
        """A module never had a `step()` to miss, so the object wording is a
        non sequitur about it."""
        silent = _hollow.silent_benches(project, C.load(project))
        assert "function(s)" in silent[0].describe()


class TestZeroChurnForEverythingElse:
    def test_a_module_with_no_functions_gets_neither(self, tmp_path):
        dest = tmp_path / "p2"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p2", dest, modules=["holder"])
        assert not (dest / "native/tests/test_holder_core.c").exists()
        assert not (dest / "native/benchmarks/bench_holder_core.c").exists()
        assert "bench_holder_core" not in (
            dest / "native/src/holder/CMakeLists.txt"
        ).read_text(encoding="utf-8")

    def test_targets_block_is_empty_without_functions(self):
        assert R.module_targets_block("holder", False) == ""

    def test_adding_an_object_does_not_remove_the_module_targets(
        self, project
    ):
        """Keyed on "declares a function", not on "declares no objects".

        A function-only predicate would mean adding an object to the module
        silently DELETED its test and benchmark targets — and `jm apply`
        would then dutifully reconcile the files away too.
        """
        with contextlib.redirect_stdout(io.StringIO()):
            object_run(
                project,
                "widget",
                module="util",
                arg_type="float",
                return_type="float",
                state_vars=[("g", "double", "1.0")],
            )
        cmake = _module_cmake(project)
        assert "add_executable(bench_util_core" in cmake
        assert _bench_c(project).is_file()


class TestCreateOnly:
    def test_a_written_benchmark_is_never_stamped_over(self, project):
        """The one thing jm cannot produce is the measurement, so the moment
        the file has one it is the author's."""
        _bench_c(project).write_text("/* mine */\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            fn_run(
                project,
                "ema_alpha",
                module="util",
                return_type="double",
                params=[("t", "double")],
            )
        assert _bench_c(project).read_text(encoding="utf-8") == "/* mine */\n"


class TestItCompiles:
    """The scaffold must build and the test must pass from day 0."""

    def test_cmake_builds_and_ctest_passes(self, project):
        if not __import__("shutil").which("cmake"):
            pytest.skip("cmake not available")
        cfg = subprocess.run(
            ["cmake", "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert cfg.returncode == 0, cfg.stderr
        b = subprocess.run(
            ["cmake", "--build", "build", "--target", "test_util_core"],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert b.returncode == 0, b.stdout + b.stderr
        # Through ctest, not the binary path: cmake puts it under the
        # module's build directory, and `add_test` is half of what this
        # feature adds — running it any other way would not check that.
        run = subprocess.run(
            ["ctest", "--test-dir", "build", "-R", "test_util_core"],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert "1 test" in run.stdout or "100% tests passed" in run.stdout
