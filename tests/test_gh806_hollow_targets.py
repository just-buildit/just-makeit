"""gh-806: a scaffold displaces a real test/bench and nothing says so.

The reported sequence, reproduced here end to end rather than approximated: a
component is renamed, `jm apply` materialises `test_<new>_core.c` /
`bench_<new>_core.c` and re-renders the CMake that builds *those* names, and
the author's real files — still under the old name — are compiled by nothing.

What made it survive weeks on doppler is that the replacement **passes**. So
the assertions here are deliberately paired: it is not enough that the orphan
is found, the scaffold that took its target must also be shown to be a
scaffold, and `jm status --check` must actually come back non-zero. A test
that only checked the finding exists would pass against a build where the
finding never reaches the gate — which is the same silence, one layer up.

The runtime half (a test that prints how many checks it ran, a benchmark that
says when it recorded none) is compiled and executed where a C compiler is
available. Asserting on the generated *text* would prove the template contains
a printf, not that the number it prints is right — and the first draft of the
check counter was wrong by one, in exactly the direction that keeps the banner
showing after a real suite has been added.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _hollow  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

#: The generated CMake asks for C99 with GNU extensions on (it never sets
#: C_EXTENSIONS OFF), which is what makes `struct timespec` visible to the
#: benchmark. Compiling with a bare `-std=c99` here would fail on jm's own
#: output for a reason the real build does not have.
_STD = "-std=gnu99"


def _quiet(fn, *a, **kw):
    """Run a jm command with its chatter captured."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        fn(*a, **kw)
    return buf.getvalue()


def _project(tmp_path, comp="telemetry", *, no_step=False) -> Path:
    root = tmp_path / "proj"
    _quiet(new_run, "proj", root)
    _quiet(
        object_run,
        root,
        comp,
        None,
        state_vars=[("gain", "float", "0.0f")],
        no_step=no_step,
    )
    return root


def _rename(root: Path, old: str, new: str) -> None:
    """Rename a component the way a real migration does.

    The manifest section, the native directories and every occurrence in the
    sources move; the C **test and benchmark keep their old names**, which is
    precisely the part nobody notices, and precisely what jm then fills in
    with a scaffold.
    """
    # The manifest section, wherever it lives — inline for a project built
    # through the API, `objects/<name>.toml` once it has been split.
    frag = root / "objects" / f"{old}.toml"
    if frag.is_file():
        frag.write_text(
            frag.read_text(encoding="utf-8").replace(old, new),
            encoding="utf-8",
        )
        frag.rename(root / "objects" / f"{new}.toml")
    else:
        manifest = root / C.FILENAME
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            .replace(f"[{old}]", f"[{new}]")
            .replace(f"[[{old}.", f"[[{new}."),
            encoding="utf-8",
        )

    (root / "native" / "inc" / old).rename(root / "native" / "inc" / new)
    (root / "native" / "inc" / new / f"{old}_core.h").rename(
        root / "native" / "inc" / new / f"{new}_core.h"
    )
    (root / "native" / "src" / old).rename(root / "native" / "src" / new)
    for suffix in ("_core.c", "_ext.c"):
        (root / "native" / "src" / new / f"{old}{suffix}").rename(
            root / "native" / "src" / new / f"{new}{suffix}"
        )

    cap_old, cap_new = (
        old.title().replace("_", ""),
        new.title().replace("_", ""),
    )
    touched = [
        root / "native" / "inc" / new / f"{new}_core.h",
        root / "native" / "src" / new / f"{new}_core.c",
        root / "native" / "src" / new / f"{new}_ext.c",
        root / "native" / "src" / new / "CMakeLists.txt",
        root / "native" / "inc" / "proj.h",
        root / "CMakeLists.txt",
        root / "src" / "proj" / "__init__.py",
    ]
    for path in touched:
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace(old, new)
            .replace(cap_old, cap_new),
            encoding="utf-8",
        )
    for stale in (
        root / "src" / "proj" / f"{old}.pyi",
        root / "src" / "proj" / "tests" / f"test_{old}.py",
        root / "src" / "proj" / "benchmarks" / f"bench_{old}.py",
    ):
        stale.unlink(missing_ok=True)


def _mark_as_real_suite(root: Path, comp: str) -> None:
    """Give the C test enough content to stand in for a 500-line suite."""
    path = root / "native" / "tests" / f"test_{comp}_core.c"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n/* the author's suite */\n" * 400,
        encoding="utf-8",
    )


@pytest.fixture()
def renamed(tmp_path) -> Path:
    """The reported tree: `telemetry` -> `dp_tlm`, apply already run."""
    root = _project(tmp_path)
    _mark_as_real_suite(root, "telemetry")
    _rename(root, "telemetry", "dp_tlm")
    _quiet(apply_run, root)
    return root


class TestTheReportedSequence:
    """The rename really does displace both files, and jm really does say so."""

    def test_apply_materialises_a_scaffold_over_the_renamed_target(
        self, renamed
    ):
        # The half that makes this expensive: what took the target is a
        # scaffold, and it passes.
        scaffold = renamed / "native" / "tests" / "test_dp_tlm_core.c"
        assert scaffold.is_file()
        assert "JM_SCAFFOLD_CHECKS" in scaffold.read_text(encoding="utf-8")

    def test_the_real_suite_is_still_on_disk_and_still_unbuilt(self, renamed):
        real = renamed / "native" / "tests" / "test_telemetry_core.c"
        assert real.is_file(), "the orphan is not deleted — that is the point"
        for cml in renamed.rglob("CMakeLists.txt"):
            assert "test_telemetry_core" not in cml.read_text(encoding="utf-8")

    def test_both_halves_are_reported_not_just_the_benchmark(self, renamed):
        cfg = C.load(renamed)
        found = {o.kind: o for o in _hollow.orphans(renamed, cfg)}
        assert set(found) == {"test", "bench"}
        # The line count is what tells a reader a real suite was displaced
        # rather than an empty one, so it is a measurement, not a flag.
        assert found["test"].lines > 400
        assert found["test"].declared is False

    def test_apply_says_so_on_stderr_and_marks_it_gating(self, renamed):
        out = _quiet(apply_run, renamed)
        assert "test_telemetry_core.c" in out
        assert "warning !:" in out
        assert "fail `jm status --check`" in out

    def test_status_check_fails_instead_of_saying_up_to_date(self, renamed):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(renamed, check=True)
        text = buf.getvalue()
        assert drift == 2, text
        assert "UNBUILT (2)" in text
        # gh-767's rule: the tree is not "up to date" while a real suite in it
        # is compiled by nothing.
        assert "OK — up to date" not in text


class TestNoFalsePositives:
    """Every orphan reported sends someone to delete or wire up a file."""

    def test_a_freshly_scaffolded_project_is_silent(self, tmp_path):
        root = _project(tmp_path, "fir")
        cfg = C.load(root)
        assert _hollow.orphans(root, cfg) == []
        assert _hollow.silent_benches(root, cfg) == []

    def test_declared_selects_between_two_different_diagnoses(self, tmp_path):
        """gh-847. `.declared` is the product, and nothing pinned it.

        The stem-only assertions this file used could not fail against the
        code they were written to protect: `orphans()` builds one `Orphan` per
        matching **file**, and `declared` never drives that loop. The gh-837
        change removed a `module_objects` union that `C.components` already
        subsumed — so it altered nothing observable, `.declared` included, and
        a stem list was identical either way.

        Tightening those to `(stem, declared)` is worth doing but is not
        sufficient on its own: a ghost is undeclared under both versions. What
        was actually untested is the **branch** — `.declared` chooses between
        "the wiring was removed or hand-edited" and "this is the shape a
        component rename leaves behind", which are different instructions to
        a reader. So exercise both values, and assert the sentences.
        """
        root = _project(tmp_path, "fir")
        # A SECOND component, so the tree still builds a benchmark after the
        # first one's target is removed. Without it this test trips the
        # gh-832 capability rule — no bench targets anywhere means the whole
        # kind stands down — which is that trade-off showing up for real
        # rather than only in a docstring.
        _quiet(
            object_run,
            root,
            "iir",
            None,
            state_vars=[("gain", "float", "0.0f")],
        )
        bench_dir = root / "native" / "benchmarks"
        # (a) a file named after NO component — the rename shape.
        (root / "native" / "tests" / "test_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        # (b) a file named after a component that IS declared, whose target
        # has been removed — same orphan, different cause, different advice.
        (bench_dir / "bench_fir_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        cml = root / "native" / "src" / "fir" / "CMakeLists.txt"
        cml.write_text(
            "\n".join(
                ln
                for ln in cml.read_text(encoding="utf-8").split("\n")
                if "bench_fir_core" not in ln
            ),
            encoding="utf-8",
        )
        found = {o.stem: o for o in _hollow.orphans(root, C.load(root))}
        assert set(found) == {"ghost", "fir"}
        assert found["ghost"].declared is False
        assert found["fir"].declared is True
        # The reason `.declared` matters at all:
        assert "leaves behind" in found["ghost"].describe()
        assert "removed or hand-edited" in found["fir"].describe()
        assert "leaves behind" not in found["fir"].describe()

    def test_a_wildcard_build_file_stands_the_scan_down(self, tmp_path):
        # `file(GLOB ...)` makes "is this compiled?" unanswerable by reading,
        # so the scan declines rather than guessing.
        root = _project(tmp_path, "fir")
        (root / "native" / "tests" / "test_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        cfg = C.load(root)
        assert [(o.stem, o.declared) for o in _hollow.orphans(root, cfg)] == [
            ("ghost", False)
        ]

        cml = root / "CMakeLists.txt"
        cml.write_text(
            cml.read_text(encoding="utf-8")
            + "\nfile(GLOB EXTRA native/tests/*.c)\n",
            encoding="utf-8",
        )
        assert _hollow.orphans(root, C.load(root)) == []

    def test_the_make_backend_never_builds_benches_so_none_is_an_orphan(
        self, tmp_path
    ):
        # jm's `build = "make"` path patches TARGETS and C_TESTS and emits no
        # bench rule at all. Gating on that would fail every make project's CI
        # for something no `jm apply` can clear — the gap itself is gh-832.
        root = tmp_path / "mk"
        _quiet(new_run, "mk", root, build_system="make")
        _quiet(object_run, root, "fir", None)
        assert (root / "native" / "benchmarks" / "bench_fir_core.c").is_file()
        assert _hollow.orphans(root, C.load(root)) == []

    def test_status_allow_exempts_an_orphan_from_the_gate(self, renamed):
        manifest = renamed / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "[project]",
                '[project]\nstatus_allow = ["native/tests/test_telemetry'
                '_core.c", "native/benchmarks/bench_telemetry_core.c"]',
            ),
            encoding="utf-8",
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(renamed, check=True)
        text = buf.getvalue()
        assert drift == 0
        # Exempt from the gate is not the same as invisible, and not the same
        # as in sync — both still show, and the summary still qualifies.
        assert "UNBUILT (2)" in text
        assert "[status_allow]" in text
        assert "2 unbuilt (allowed)" in text


class TestTheSilentBenchmark:
    """A `no_step` component whose methods have no benchable shape."""

    @pytest.fixture()
    def hollow_bench(self, tmp_path) -> Path:
        root = _project(tmp_path, "tlm", no_step=True)
        _quiet(
            method_run,
            root,
            "tlm",
            "read",
            None,
            "void",
            "uint64_t",
            True,
            [],
        )
        return root

    def test_the_generated_bench_records_nothing(self, hollow_bench):
        src = hollow_bench / "native" / "benchmarks" / "bench_tlm_core.c"
        text = src.read_text(encoding="utf-8")
        # gh-840 put a worked `jm_bench_add(...)` into the TODO of exactly
        # this file, so a bare substring test now matches the instructions
        # rather than a measurement. Asserted through the detector's own
        # pattern so the test and `silent_benches` cannot drift on what
        # counts as a call — which is how the substring form silently
        # disabled the whole SILENT section.
        assert _hollow._BENCH_ADD_CALL.search(text) is None
        assert "jm_bench_add(&_bench," in text, "the TODO shows the call"

    def test_it_is_reported_with_the_method_count(self, hollow_bench):
        found = _hollow.silent_benches(hollow_bench, C.load(hollow_bench))
        assert len(found) == 1
        assert found[0].component == "tlm"
        assert found[0].methods == 1

    def test_it_is_advisory_and_does_not_gate(self, hollow_bench):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            drift = _status.run(hollow_bench, check=True)
        text = buf.getvalue()
        assert drift == 0, text
        assert "SILENT (1)" in text

    def test_a_benched_component_is_not_reported(self, tmp_path):
        root = _project(tmp_path, "fir")
        assert _hollow.silent_benches(root, C.load(root)) == []

    def test_a_module_objects_bench_is_reported_once(self, tmp_path):
        # gh-836: `C.components` already returns every component — a module
        # object keeps its own top-level `[<obj>]` section — so unioning
        # `module_objects` on top of it counted each one twice and the
        # section read 2x the files (doppler: 31 files, `SILENT (62)`).
        # One object, one file, one finding.
        root = tmp_path / "modproj"
        _quiet(new_run, "modproj", root)
        _quiet(module_run, root, "m")
        _quiet(
            object_run,
            root,
            "tlm",
            "m",
            state_vars=[("gain", "float", "0.0f")],
            no_step=True,
        )
        _quiet(
            method_run, root, "tlm", "read", "m", "void", "uint64_t", True, []
        )
        benches = _hollow.silent_benches(root, C.load(root))
        assert [s.rel for s in benches] == [
            "native/benchmarks/bench_tlm_core.c"
        ]
        # And the same object must not double the orphan scan either.
        (root / "native" / "tests" / "test_ghost_core.c").write_text(
            "int main(void) { return 0; }\n", encoding="utf-8"
        )
        assert [
            (o.stem, o.declared) for o in _hollow.orphans(root, C.load(root))
        ] == [("ghost", False)]


class TestTheScaffoldCheckCount:
    """The stamped number is a measurement of the file being written."""

    def test_it_equals_the_checks_actually_in_the_file(self, tmp_path):
        root = _project(tmp_path, "fir")
        text = (root / "native" / "tests" / "test_fir_core.c").read_text(
            encoding="utf-8"
        )
        stamped = int(
            re.search(r"#define JM_SCAFFOLD_CHECKS (\d+)", text).group(1)
        )
        # Statements only. The unanchored count picked up the macro's own
        # `#define` and a comment that mentioned `CHECK()`, and overstated by
        # one — which keeps the banner showing after a real check is added.
        # (gh-934 moved the `#define` out to jm_test.h, so only the comment
        # hazard is left; the anchor still earns its place.)
        #
        # REQUIRE counts too, since gh-934: `obj_null_check` emits one, and a
        # count blind to it would understate the stamp by one — the same
        # off-by-one in the opposite direction, clearing the banner for a file
        # nobody has written a test into.
        assert stamped == len(
            re.findall(r"^\s*(?:CHECK|REQUIRE)\s*\(", text, re.M)
        )
        assert stamped > 0

    @_needs_cc
    def test_the_scaffold_reports_its_own_emptiness_when_run(self, tmp_path):
        root = _project(tmp_path, "fir")
        out = _run_c_test(root, "fir", tmp_path)
        assert "PASSED (4 checks)" in out, out
        assert "no assertions beyond" in out

    @_needs_cc
    def test_the_banner_clears_when_a_real_check_is_added(self, tmp_path):
        root = _project(tmp_path, "fir")
        src = root / "native" / "tests" / "test_fir_core.c"
        text = src.read_text(encoding="utf-8")
        src.write_text(
            text.replace(
                "    fir_destroy(obj);",
                "    CHECK(1 == 1);\n    fir_destroy(obj);",
                1,
            ),
            encoding="utf-8",
        )
        out = _run_c_test(root, "fir", tmp_path)
        assert "PASSED (5 checks)" in out, out
        assert "no assertions beyond" not in out


class TestTheEmptyBenchmarkSaysSo:
    """`jm_bench_write_json` is the choke point every bench target reaches."""

    @_needs_cc
    def test_a_bench_that_measured_nothing_says_so(self, tmp_path):
        root = _project(tmp_path, "tlm", no_step=True)
        exe = tmp_path / "bench"
        subprocess.run(
            [
                _CC,
                _STD,
                "-o",
                str(exe),
                str(root / "native" / "benchmarks" / "bench_tlm_core.c"),
                str(root / "native" / "src" / "tlm" / "tlm_core.c"),
                "-I",
                str(root / "native" / "inc"),
                "-I",
                str(root / "native" / "benchmarks"),
                "-lm",
            ],
            check=True,
            capture_output=True,
        )
        done = subprocess.run(
            [str(exe)], cwd=tmp_path, capture_output=True, text=True
        )
        assert done.returncode == 0, (
            "still exits 0 — that is the whole problem"
        )
        assert "no measurements recorded" in done.stdout, done.stdout
        assert "measures nothing" in done.stderr, done.stderr
        # The old banner promised methods that were never there.
        assert "methods below" not in done.stdout


def _run_c_test(root: Path, comp: str, tmp_path: Path) -> str:
    """Compile and run one generated C test; return its stdout."""
    exe = tmp_path / f"t_{comp}"
    subprocess.run(
        [
            _CC,
            _STD,
            "-o",
            str(exe),
            str(root / "native" / "tests" / f"test_{comp}_core.c"),
            str(root / "native" / "src" / comp / f"{comp}_core.c"),
            "-I",
            str(root / "native" / "inc"),
            "-lm",
        ],
        check=True,
        capture_output=True,
    )
    done = subprocess.run([str(exe)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout
