"""The generated benchmark must not name a POSIX-only clock (gh-1341).

`jm_bench.h` recorded timings and wrote the JSON but handed the actual clock
read back to the generated benchmark, so every `bench_<obj>_core.c` called
``clock_gettime(CLOCK_MONOTONIC)`` in its own ``main()``. Both are POSIX and
the UCRT has neither::

    error: call to undeclared function 'clock_gettime'
    error: use of undeclared identifier 'CLOCK_MONOTONIC'

In doppler that is **106 benchmark files**, and `jm_bench.h` is included by
all of them -- so one place fixes all of them, and that place is here rather
than downstream. `jm_bench.h` is vendored and create-only: a local patch is a
private copy that does not survive a re-vendor, which is not hypothetical
(doppler has lost two edits from `jm_simd.h` that way). Editing the 106
generated files is worse -- they are create-only scaffolds the author then
writes, so a fix there is 106 copies of the same three lines with no home.

**The sweep is over generated OUTPUT, not over the emitters.** There were two
independent sets of call sites -- `_context/_methods.py` and
`_context/_step.py` -- and fixing the first and believing the job done is
exactly what happened here: the benchmark still carried four
``clock_gettime`` calls from the other file. A test that reads the templates
would have agreed with the mistake; one that reads what jm actually wrote
does not.
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

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

HEADER = (
    Path(__file__).parent.parent / "src/just_makeit/templates/c/inc/jm_bench.h"
)

#: Spellings that do not exist on the UCRT. A generated benchmark naming any
#: of these does not compile on Windows.
POSIX_ONLY = ("clock_gettime", "CLOCK_MONOTONIC", "struct timespec")


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


@pytest.fixture
def benched(tmp_path) -> Path:
    """A project whose benchmark times step(), steps() AND a named method.

    All three go through different emitters, and the step/steps pair lives in
    a different module from the method one -- which is how half the fix got
    missed the first time.
    """
    root = tmp_path / "q"
    _silent(new_run, "q", root)
    _silent(object_run, root, "fir", None, state_vars=[("n", "size_t", "4")])
    _silent(
        method_run, root, "fir", "tweak", None, "float", "float", False, []
    )
    return root


class TestNoGeneratedBenchmarkNamesAPosixClock:
    def test_the_sweep_is_armed(self, benched):
        """A benchmark that times nothing would pass every check below."""
        bench = benched / "native/benchmarks/bench_fir_core.c"
        assert bench.is_file(), "no benchmark was generated"
        body = bench.read_text(encoding="utf-8")
        assert body.count("jm_bench_now_ns()") >= 6, (
            "fewer timing reads than the three timed sections imply — the "
            f"fixture is not exercising them:\n{body}"
        )

    @pytest.mark.parametrize("spelling", POSIX_ONLY)
    def test_no_posix_only_spelling_reaches_the_benchmark(
        self, benched, spelling
    ):
        bench = benched / "native/benchmarks/bench_fir_core.c"
        body = bench.read_text(encoding="utf-8")
        assert spelling not in body, (
            f"the generated benchmark still names {spelling!r}, which the "
            f"UCRT does not have"
        )

    def test_no_posix_only_spelling_reaches_any_generated_c(self, benched):
        """Wider than the one file: whatever else jm emits must not reintroduce
        it either. `jm_bench.h` is exempt -- it is where the #if lives."""
        offenders = {}
        for p in sorted(benched.rglob("*.c")) + sorted(benched.rglob("*.h")):
            if p.name == "jm_bench.h":
                continue
            body = p.read_text(encoding="utf-8")
            hits = [s for s in POSIX_ONLY if s in body]
            if hits:
                offenders[p.name] = hits
        assert not offenders, offenders


class TestTheHeaderCarriesBothBranches:
    def test_it_declares_the_timer_and_the_helper(self):
        body = HEADER.read_text(encoding="utf-8")
        assert "jm_bench_now_ns(void)" in body
        assert "jm_bench_elapsed_sec(uint64_t t0, uint64_t t1)" in body

    def test_windows_uses_qpc_and_posix_uses_clock_gettime(self):
        body = HEADER.read_text(encoding="utf-8")
        assert "QueryPerformanceCounter" in body
        assert "QueryPerformanceFrequency" in body
        assert "clock_gettime(CLOCK_MONOTONIC" in body

    def test_the_json_timer_name_is_derived_not_restated(self):
        """The JSON reported `clock_gettime` unconditionally. Deriving it from
        the same #if that picks the clock is what stops the artefact claiming
        a timer that did not run."""
        body = HEADER.read_text(encoding="utf-8")
        assert "JM_BENCH_TIMER_NAME" in body
        assert '"timer": \\"%s\\"' in body or "JM_BENCH_TIMER_NAME);" in body
        assert 'fprintf(fp, "        \\"timer\\": \\"clock_gettime' not in body


class TestTheWindowsScalingIsExactAndCannotOverflow:
    """The QPC scaling is the part with a real bug available in it.

    The obvious `counter * 1000000000 / freq` overflows 64 bits after about
    nine seconds at a 1 GHz QPC frequency -- well inside a benchmark's
    runtime, and doppler hit exactly this porting its own timing core. The
    quotient-plus-remainder form is exact and stays small.

    Replicated here rather than compiled, because the property is arithmetic:
    it holds or it does not, and no Windows runner is needed to decide.
    """

    @staticmethod
    def _formula() -> str:
        body = HEADER.read_text(encoding="utf-8")
        start = body.index("jm_bench_now_ns(void)")
        return body[start : body.index("#else", start)]

    def test_the_naive_product_is_not_used(self):
        """`c.QuadPart * 1000000000` anywhere in the Windows branch is the
        overflow, whatever is divided into it afterwards."""
        win = self._formula()
        flat = re.sub(r"\s+", " ", win)
        assert "QuadPart * 1000000000" not in flat, (
            f"the overflowing product is back:\n{win}"
        )
        assert "%" in win and "/" in win, (
            "the quotient-plus-remainder split is gone"
        )

    @pytest.mark.parametrize(
        "freq", [10_000_000, 1_000_000_000, 3_579_545, 24_000_000]
    )
    def test_it_is_exact_against_the_rational_value(self, freq):
        """Exactness matters: a benchmark that loses the sub-tick remainder
        reports a quantised time, which looks like a real measurement."""
        for secs in (0.5, 9.0, 60.0, 3600.0, 86400.0):
            c = int(secs * freq)
            split = (c // freq) * 1_000_000_000 + (
                c % freq
            ) * 1_000_000_000 // freq
            exact = c * 1_000_000_000 // freq
            assert split == exact, (freq, c, split, exact)

    @pytest.mark.parametrize("freq", [1_000_000_000, 10_000_000])
    def test_the_split_stays_inside_64_bits_where_the_product_does_not(
        self, freq
    ):
        """The regression this shape exists to prevent, stated as a number."""
        u64 = 2**64 - 1
        c = int(86400 * freq)  # one day of uptime, not an exotic value
        assert c * 1_000_000_000 > u64, (
            "pick a longer uptime — this case no longer demonstrates the "
            "overflow it is guarding against"
        )
        assert (c // freq) * 1_000_000_000 <= u64
        assert (c % freq) * 1_000_000_000 <= u64


@pytest.mark.skipif(
    not shutil.which("cmake")
    or not any(shutil.which(c) for c in ("cc", "gcc", "clang")),
    reason="no C toolchain",
)
class TestItStillBuildsAndMeasures:
    def test_the_benchmark_builds_and_reports_a_sane_time(self, benched):
        """Compiling is not enough — a timer that returns a constant compiles
        and reports a benchmark of zero."""
        import json

        r = subprocess.run(
            ["cmake", "-B", "build", "-S", ".", "-DCMAKE_BUILD_TYPE=Release"],
            cwd=benched,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        r = subprocess.run(
            ["cmake", "--build", "build", "--target", "bench_fir_core"],
            cwd=benched,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr

        exe = next(benched.rglob("bench_fir_core"), None)
        assert exe and exe.is_file(), "no benchmark binary was produced"
        r = subprocess.run(
            [str(exe)], cwd=exe.parent, capture_output=True, text=True
        )
        assert r.returncode == 0, r.stdout + r.stderr

        out = next(exe.parent.glob("bench_fir_core.json"), None)
        assert out, "the benchmark produced no JSON"
        stats = json.loads(out.read_text())["benchmarks"][0]
        assert stats["options"]["timer"] == "clock_gettime"
        s = stats["stats"]
        assert s["mean"] > 0, "the timer reported zero elapsed — it is dead"
        assert s["min"] <= s["mean"] <= s["max"], s
