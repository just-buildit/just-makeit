"""gh-1915, gh-1833, gh-1841: `jm bench` finds, prints and keeps its results.

Three properties of ``_bench``, each reproduced on ``main`` before the fix:

* **gh-1915** -- pytest collects only ``test_*.py`` by default, so the
  ``bench_*.py`` a generated project writes was never run, and ``jm bench``
  reported ``Python benchmarks: none found`` with exit 0. Collection now uses
  the set doppler's own pyproject names.
* **gh-1833** -- ``jm bench --check --json`` owns stdout for one JSON
  document, but the build's progress and the children's output went to the
  same stream first, so the document did not parse. Everything else now goes
  to stderr for that run.
* **gh-1841** -- pytest-benchmark writes its JSON once, at the end of a
  session, so one timeout over the whole tree discarded every Python result.
  Each file is its own run under its own budget.

Every test drives jm through ``run_cli`` and benchmarks a real scaffolded
project. The slow benchmark is a ``time.sleep`` past a 15 s budget, the cheapest
faithful slow run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

FAST_BENCH = """\
def test_fast_bench(benchmark):
    benchmark(lambda: sum(range(100)))
"""

SLOW_BENCH = """\
import time


def test_slow_bench(benchmark):
    benchmark(lambda: time.sleep(300))
"""


def _bench_project(tmp_path: Path) -> Path:
    """A pytest-benchmark scaffold whose ``.venv`` is the test's interpreter.

    A wrapper, not a symlink: a symlink run from here loses its venv's
    site-packages (python finds the venv from the path it was launched by),
    so pytest-benchmark would look absent.
    """
    # The wrapped interpreter is this test's own. A pytest-benchmark it lacks
    # would make `jm bench` report "not installed" and skip every case, which
    # reads as a pass. Fail here instead, and name the declaration to fix.
    assert importlib.util.find_spec("pytest_benchmark") is not None, (
        "pytest-benchmark is missing from the test interpreter; declare it "
        "in PYTEST_DEPS (Makefile)"
    )
    r = run_cli(
        "new", "p", "--object", "g", "--pytest-benchmark", cwd=tmp_path
    )
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "p"
    interp = proj / ".venv" / "bin" / "python"
    interp.parent.mkdir(parents=True)
    interp.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    interp.chmod(0o755)
    return proj


def _snapshot(proj: Path, tag: str) -> dict:
    """The Python snapshot ``jm bench --tag <tag>`` saved."""
    return json.loads(
        (proj / "benchmarks" / "history" / f"{tag}.json").read_text()
    )


def test_1915_generated_bench_file_is_run(tmp_path):
    """gh-1915: the scaffold's ``bench_g.py`` is collected and its benchmarks
    are recorded.

    On main this prints ``Python benchmarks: none found.`` and exits 0, with
    ``8 skipped``: the benchmarks were collected by nothing.
    """
    proj = _bench_project(tmp_path)

    r = run_cli("bench", "--python-only", "--tag", "gate", cwd=proj)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "none found" not in r.stdout, r.stdout
    names = [b["fullname"] for b in _snapshot(proj, "gate")["benchmarks"]]
    assert any("bench_g.py" in n for n in names), names


def test_1833_check_json_stdout_is_only_the_document(tmp_path):
    """gh-1833: ``--check --json`` writes one parseable JSON document to
    stdout, and the build and benchmark progress go to stderr.

    On main the build's ``build      project`` line and cmake's output come
    first on stdout, so ``json.loads`` raises ``JSONDecodeError``.

    cmake's own output comes from a child process on file descriptor 1;
    ``run_cli`` captures that too, so it is checked on ``r.stdout`` directly.
    """
    proj = _bench_project(tmp_path)

    r = run_cli("bench", "--check", "--json", "--python-only", cwd=proj)

    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(r.stdout)
    assert doc["missing_baseline"] == ["Python"], doc
    assert "build      project" in r.stderr, r.stderr
    assert "Built target" in r.stderr, r.stderr
    assert "Built target" not in r.stdout, r.stdout


def test_1841_one_slow_file_costs_only_itself(tmp_path):
    """gh-1841: a file past the budget loses its own results, and the other
    files' results are kept, with the skipped file named in the snapshot.

    The fast benchmark is in its own file, so only the slow file's own run is
    lost. On main the single session is killed at the budget and the fast
    result goes with it: the snapshot has no Python entries at all.
    """
    proj = _bench_project(tmp_path)
    # The scaffold's own benchmark runs for longer than the budget below, so
    # it would be timed out too; only the two files this test writes are timed.
    (proj / "src" / "p" / "benchmarks" / "bench_g.py").unlink()
    (proj / "src" / "p" / "tests").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "p" / "tests" / "test_fast_bench.py").write_text(
        FAST_BENCH
    )
    (proj / "src" / "p" / "tests" / "test_slow_bench.py").write_text(
        SLOW_BENCH
    )
    with (proj / "just-makeit.toml").open("a", encoding="utf-8") as f:
        f.write("\n[project.bench]\ntimeout = 15\n")

    r = run_cli("bench", "--python-only", "--tag", "gate", cwd=proj)

    assert r.returncode == 1, r.stdout + r.stderr
    snap = _snapshot(proj, "gate")
    names = [b["fullname"] for b in snap["benchmarks"]]
    assert any("test_fast_bench" in n for n in names), names
    assert not any("test_slow_bench" in n for n in names), names
    # gh-2135: the slow file is retried one benchmark at a time, so the entry
    # names the benchmark, not the file.
    assert any(
        t.endswith("test_slow_bench.py::test_slow_bench")
        for t in snap["timed_out"]
    ), snap["timed_out"]


MIXED_BENCH = """\
import time


def test_fast_bench(benchmark):
    benchmark(lambda: sum(range(100)))


def test_slow_bench(benchmark):
    benchmark(lambda: time.sleep(300))
"""


def test_2135_a_fast_and_a_slow_benchmark_in_one_file(tmp_path):
    """gh-2135: a file with one fast and one slow benchmark keeps the fast
    result and names the slow benchmark in timed_out.

    On the file-level budget alone the whole file is lost, the fast result
    too, because pytest-benchmark writes its JSON once per run. The file is
    run again one benchmark at a time, each under the same budget, so only the
    slow one costs anything.
    """
    proj = _bench_project(tmp_path)
    (proj / "src" / "p" / "benchmarks" / "bench_g.py").unlink()
    (proj / "src" / "p" / "tests").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "p" / "tests" / "test_mixed_bench.py").write_text(
        MIXED_BENCH
    )
    with (proj / "just-makeit.toml").open("a", encoding="utf-8") as f:
        f.write("\n[project.bench]\ntimeout = 15\n")

    r = run_cli("bench", "--python-only", "--tag", "mixed", cwd=proj)

    assert r.returncode == 1, r.stdout + r.stderr
    snap = _snapshot(proj, "mixed")
    names = [b["fullname"] for b in snap["benchmarks"]]
    assert any("test_fast_bench" in n for n in names), names
    assert not any("test_slow_bench" in n for n in names), names
    assert any(
        t.endswith("test_mixed_bench.py::test_slow_bench")
        for t in snap["timed_out"]
    ), snap["timed_out"]


def test_a_bench_file_that_does_not_import_fails_loudly(tmp_path):
    """review of #2145: a benchmark file that fails to collect is an error
    that names the file, not a quiet drop from the results.

    pytest exits non-zero for a collection error and still lists the files
    that did collect, so a return on the listing alone would report fewer
    Python results with exit 0. The gate is the scaffold plus one file that
    cannot import.
    """
    proj = _bench_project(tmp_path)
    broken = proj / "src" / "p" / "benchmarks" / "bench_broken.py"
    broken.write_text("import no_such_module_for_jm_gate\n")

    r = run_cli("bench", "--python-only", cwd=proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "bench_broken.py" in r.stderr, r.stderr


IMPORT_SLEEP_BENCH = """\
import time

time.sleep(60)


def test_never_reached(benchmark):
    benchmark(lambda: None)
"""


def test_2135_a_file_slow_to_import_is_named_within_three_budgets(tmp_path):
    """gh-2135: a file whose import outlasts the budget is named, and jm stops
    in about three budgets: the whole-tree listing, the file's run, and the
    file's own listing. Without the listings' timeouts it would wait out the
    import, which is worse than main."""
    import time as _time

    proj = _bench_project(tmp_path)
    (proj / "src" / "p" / "benchmarks" / "bench_g.py").unlink()
    (proj / "src" / "p" / "tests").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "p" / "tests" / "test_import_slow.py").write_text(
        IMPORT_SLEEP_BENCH
    )
    with (proj / "just-makeit.toml").open("a", encoding="utf-8") as f:
        f.write("\n[project.bench]\ntimeout = 8\n")

    t0 = _time.monotonic()
    r = run_cli("bench", "--python-only", "--tag", "slowimport", cwd=proj)
    elapsed = _time.monotonic() - t0

    assert r.returncode == 1, r.stdout + r.stderr
    # Three budgets: the whole-tree listing, the file's run, and the file's own
    # listing, each at most 8 s. Without the bounds it waits out the 60 s import.
    assert elapsed < 4 * 8, (
        f"took {elapsed:.0f} s, more than three 8 s budgets"
    )
    # No benchmark is ever listed, so no snapshot is written; the file is named
    # on the output, as every timed-out run is.
    out = r.stdout + r.stderr
    assert (
        "timeout    pytest --benchmark-only src/p/tests/test_import_slow.py"
        in out
    ), out
