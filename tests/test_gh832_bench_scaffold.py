"""gh-832 + gh-840: the bench scaffold holding up its end of green-from-day-0.

jm's contract is *scaffold a project, be green from day 0, then fill in the
TODOs*. A generated `bench_<comp>_core.c` broke it in two ways, one per issue:

* **gh-832** — on the `build = "make"` backend the source was written and
  nothing built it. `make bench` did not exist. So the TODO was not
  *fillable*: you could write a perfect benchmark and no command would run it.
* **gh-840** — the file did not say it was unfinished. No template jm ships
  contained a `TODO`, the timing helper it wrote was never called, and
  `jm_bench_add` — the one call that puts a measurement in the JSON — appeared
  in no generated stub. So the TODO was not *legible* either.

The dead-code half is asserted with `-Werror`, not by grepping for absence.
Four unused-symbol warnings is the measurable form of "this file contains
scaffolding nobody wired up", and it is also a real build break for a project
that compiles its benchmarks with warnings-as-errors.

`_hollow.orphans`' backend carve-out (added with gh-806, when the make gap was
total) is replaced here by a capability check, so it self-clears rather than
naming a backend that has since been fixed.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _hollow  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_MAKE = shutil.which("make")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        fn(*a, **kw)
    return buf.getvalue()


def _make_project(tmp_path, *comps: str) -> Path:
    root = tmp_path / "mk"
    _quiet(new_run, "mk", root, build_system="make")
    for comp in comps:
        _quiet(object_run, root, comp, None)
    return root


def _unfillable(tmp_path) -> Path:
    """A `no_step` component whose only method jm cannot size at bench time.

    This is doppler's exact shape, and the population that gh-840 is about.
    """
    root = tmp_path / "proj"
    _quiet(new_run, "proj", root)
    _quiet(object_run, root, "tlm", None, no_step=True)
    _quiet(method_run, root, "tlm", "read", None, "void", "uint64_t", True, [])
    return root


def _bench_src(root: Path, comp: str) -> str:
    return (root / "native" / "benchmarks" / f"bench_{comp}_core.c").read_text(
        encoding="utf-8"
    )


class TestTheMakeBackendCanBuildABenchmark:
    """gh-832: the source was written and nothing compiled it."""

    def test_the_makefile_declares_and_wires_every_component(self, tmp_path):
        root = _make_project(tmp_path, "fir", "iir")
        mf = (root / "Makefile").read_text(encoding="utf-8")
        assert "C_BENCHES := bench_fir_core bench_iir_core" in mf
        assert "\nbench: $(C_BENCHES)" in mf
        # Cleaned like the tests are, and advertised like the tests are —
        # `help` is generated from this file, so an unlisted target is one
        # nobody finds.
        assert "rm -f $(TARGETS) $(C_TESTS) $(C_BENCHES)" in mf
        assert "make bench" in mf

    def test_the_compile_rule_uses_gnu99_not_c99(self, tmp_path):
        # Measured, not stylistic: `struct timespec` is not visible under
        # strict c99, so the benchmark's own timing helper does not compile.
        # The test rule beside it legitimately uses -std=c99. The cmake
        # backend works today only because CMake leaves extensions on.
        root = _make_project(tmp_path, "fir")
        mf = (root / "Makefile").read_text(encoding="utf-8")
        rule = mf[mf.index("bench_fir_core: ") :].split("\n\n")[0]
        assert "-std=gnu99" in rule
        assert "-Inative/benchmarks" in rule
        assert "-lm" in rule

    @pytest.mark.skipif(
        _CC is None or _MAKE is None, reason="needs make and a C compiler"
    )
    def test_make_bench_actually_builds_and_runs(self, tmp_path):
        root = _make_project(tmp_path, "fir")
        done = subprocess.run(
            [_MAKE, "bench"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "=== fir benchmark ===" in done.stdout, done.stdout
        # A real measurement reached the JSON, which is the whole point of
        # the target existing.
        assert (root / "bench_fir_core.json").is_file()


class TestTheOrphanGateFollowsCapabilityNotBackend:
    """gh-806's carve-out named a backend; gh-832 fixed that backend."""

    def _ghost(self, root: Path) -> None:
        (root / "native" / "benchmarks" / "bench_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )

    def test_a_make_project_is_now_under_the_gate(self, tmp_path):
        root = _make_project(tmp_path, "fir")
        self._ghost(root)
        assert [o.stem for o in _hollow.orphans(root, C.load(root))] == [
            "ghost"
        ]

    def test_a_tree_that_builds_no_benchmark_still_is_not(self, tmp_path):
        # The gh-767 rule: do not fail a gate for something no `jm apply` can
        # clear. A project predating gh-832 has no bench rules at all, so the
        # whole category is unbuilt by construction rather than orphaned.
        root = _make_project(tmp_path, "fir")
        mf = root / "Makefile"
        mf.write_text(
            "\n".join(
                ln
                for ln in mf.read_text(encoding="utf-8").split("\n")
                if "bench" not in ln
            ),
            encoding="utf-8",
        )
        self._ghost(root)
        assert _hollow.orphans(root, C.load(root)) == []

    def test_the_test_kind_is_unaffected_by_all_of_this(self, tmp_path):
        root = _make_project(tmp_path, "fir")
        (root / "native" / "tests" / "test_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        assert [o.kind for o in _hollow.orphans(root, C.load(root))] == [
            "test"
        ]


class TestTheStubSaysItIsUnfinished:
    """gh-840: 'fill in the TODOs' named nothing an author could act on."""

    def test_it_carries_a_todo(self, tmp_path):
        src = _bench_src(_unfillable(tmp_path), "tlm")
        assert "TODO: benchmark this component." in src

    def test_it_names_the_candidate_methods(self, tmp_path):
        # The same list `SILENT` counts when it says `(N method(s), none
        # benchable)` — jm has it at report time, so it has it at render time.
        src = _bench_src(_unfillable(tmp_path), "tlm")
        assert "Candidates:" in src
        assert "tlm_read(obj, ...)" in src

    def test_it_shows_the_one_call_that_finishes_it(self, tmp_path):
        # `jm_bench_add` appeared in zero generated stubs: the API the
        # scaffold exists to standardise was never shown by the scaffold.
        src = _bench_src(_unfillable(tmp_path), "tlm")
        assert "jm_bench_add(&_bench," in src

    def test_a_populated_bench_has_no_todo(self, tmp_path):
        root = tmp_path / "p"
        _quiet(new_run, "p", root)
        _quiet(object_run, root, "fir", None)
        src = _bench_src(root, "fir")
        assert "TODO" not in src
        # ...and it really is populated, so the assertion above is not
        # passing because nothing was generated.
        assert "jm_bench_add(&_bench," in src

    def test_the_comment_block_is_uniformly_indented(self, tmp_path):
        # A generated comment is not something clang-format straightens out,
        # and the first draft emitted continuation lines at column 0.
        src = _bench_src(_unfillable(tmp_path), "tlm")
        block = src[
            src.index("    /* TODO:") : src.index(
                "*/", src.index("    /* TODO:")
            )
        ]
        for line in block.split("\n")[1:]:
            if line.strip():
                assert line.startswith("     *"), repr(line)


class TestTheTodoDoesNotDisableTheDetector:
    """The interaction this change nearly shipped, and the suite caught.

    gh-806's `SILENT` finds an empty benchmark by looking for `jm_bench_add`.
    gh-840 puts a worked `jm_bench_add(...)` into the `TODO` of exactly the
    files `SILENT` exists to report — so the original substring test matched
    the *instructions saying the file is empty* and concluded it was full.
    `SILENT` stopped firing at all.

    A feature whose example text defeats the detector for the condition the
    example is about is not an obvious failure to look for; it is only
    obvious once something asserts both at once, which is what this does.
    """

    def test_an_unfillable_bench_is_still_reported_as_silent(self, tmp_path):
        root = _unfillable(tmp_path)
        found = _hollow.silent_benches(root, C.load(root))
        assert [s.component for s in found] == ["tlm"]
        # ...and the TODO really does mention the call, so the assertion
        # above is passing despite the mention rather than in its absence.
        assert "jm_bench_add(&_bench," in _bench_src(root, "tlm")

    def test_a_real_call_still_counts_as_populated(self, tmp_path):
        root = tmp_path / "p"
        _quiet(new_run, "p", root)
        _quiet(object_run, root, "fir", None)
        assert _hollow.silent_benches(root, C.load(root)) == []


class TestItShipsNoDeadCode:
    """The compiler is the assertion; grep for absence would prove less."""

    @_needs_cc
    def test_an_unfillable_bench_compiles_clean_under_werror(self, tmp_path):
        # Was four warnings: `t0`, `t1`, `elapsed_sec` and `jm_bench_add`, all
        # unused. A project building its benchmarks with -Werror could not
        # compile a jm scaffold at all.
        root = _unfillable(tmp_path)
        done = subprocess.run(
            [
                _CC,
                "-std=gnu99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-c",
                "-o",
                "/dev/null",
                str(root / "native" / "benchmarks" / "bench_tlm_core.c"),
                "-I",
                str(root / "native" / "inc"),
                "-I",
                str(root / "native" / "benchmarks"),
            ],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr

    def test_the_helper_and_timers_follow_the_timing_block(self, tmp_path):
        empty = _bench_src(_unfillable(tmp_path), "tlm")
        # Not emitted when nothing times — but the TODO carries a
        # copy-pasteable copy of both, so the author is not left to write
        # them from scratch.
        assert "\nelapsed_sec(struct timespec" not in empty
        assert "\n    struct timespec t0, t1;" not in empty
        assert "*   elapsed_sec(struct timespec" in empty
        assert "*   struct timespec t0, t1;" in empty

    def test_a_populated_bench_still_declares_them(self, tmp_path):
        root = tmp_path / "p"
        _quiet(new_run, "p", root)
        _quiet(object_run, root, "tlm", None, no_step=True)
        # A benchable method brings the timing block, and with it the helper.
        _quiet(
            method_run,
            root,
            "tlm",
            "run",
            None,
            "float",
            "float",
            False,
            [],
        )
        src = _bench_src(root, "tlm")
        assert "elapsed_sec(struct timespec" in src
        assert "struct timespec t0, t1;" in src
        assert "TODO" not in src
