"""gh-2189: a benchmark run that fails fails `jm bench`.

`_collect_c` ran each ``bench_<comp>_core`` with its exit status unread, and
skipped a component without a word when no JSON appeared. So a benchmark
that crashed, asserted, or refused to write a short set (doppler's guard
against gh-2188's silent 32-row cap) was simply absent from a run that
exited 0, and a committed snapshot carried fewer rows than were measured.

What holds now, each asserted where it is decided:

* a non-zero exit, a death by signal, an exit 0 with no JSON and an exit 0
  with a JSON that does not parse are each reported as ``failed``, naming the
  component and why, with the binary's stderr quoted under it;
* the rest of the benchmarks still run, and `run` saves them, marks the
  snapshot with what failed, then exits 1 -- as a timeout does (gh-1687);
* a JSON a failed binary did write is not read;
* a target that built but whose binary cannot be found is a failure too, not
  a skip;
* `--check` fails on it too, and its JSON document names it.

The Python side had the same hole: `_run_one` never read pytest's exit
status, so a benchmark that raised dropped out of the snapshot and the run
exited 0. A pytest run that exits other than 0 or 5 (nothing collected) is
now ``failed`` the same way, its JSON unread -- pytest-benchmark keeps the
timing of a test that failed after its benchmark ran.

The C binaries are shell scripts standing in for ``bench_<comp>_core``, as
in `test_gh1687_bench_timeout`: what is under test is what jm does around a
run that fails, and a script that exits 1 is a faithful failed run without a
C build per case. The Python benchmarks are real pytest-benchmark runs over
a ``src/`` with no project around it, which is all `_run_python` reads.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _bench as B  # noqa: E402
from just_makeit import _config as C  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake bench binaries are sh scripts"
)

#: A good benchmark: writes the JSON `jm_bench_write_json` would, one entry,
#: and says something on stderr, which must still reach ours.
_GOOD = """#!/bin/sh
echo 'note from {stem}' >&2
cat > {stem}.json <<'EOF'
{{"benchmarks": [{{"name": "step", "stats": {{"min": 1e-6, "max": 2e-6,
 "mean": 1.5e-6, "stddev": 1e-7, "median": 1.5e-6, "ops": 6.6e5}}}}]}}
EOF
"""

#: Each way a binary fails, and the reason jm must give for it.
_BAD = {
    "exit 1": (
        "#!/bin/sh\necho 'refusing a short set' >&2\nexit 1\n",
        "exit 1",
    ),
    "exit 0, no JSON": (
        "#!/bin/sh\necho 'cannot open the JSON' >&2\nexit 0\n",
        "exit 0, wrote no JSON",
    ),
    "exit 0, bad JSON": (
        "#!/bin/sh\necho 'truncated' >&2\n"
        "printf '{{\"benchmarks\": [' > {stem}.json\n",
        "exit 0, its JSON does not parse",
    ),
    # A binary that wrote a whole JSON and then failed: the binary said its
    # run is not good, so its rows must not be read.
    "JSON then exit 3": (
        "#!/bin/sh\n"
        'echo \'{{"benchmarks": [{{"name": "step", "stats": {{"min": 1,'
        ' "mean": 1}}}}]}}\' > {stem}.json\n'
        "echo 'teardown failed' >&2\nexit 3\n",
        "exit 3",
    ),
    "signal": (
        "#!/bin/sh\necho 'about to abort' >&2\nkill -ABRT $$\n",
        "signal 6",
    ),
}

#: The stderr line each failing script writes, which the report must quote.
_SAID = {
    "exit 1": "refusing a short set",
    "exit 0, no JSON": "cannot open the JSON",
    "exit 0, bad JSON": "truncated",
    "JSON then exit 3": "teardown failed",
    "signal": "about to abort",
}


def _project(tmp_path: Path, monkeypatch, bad: str) -> Path:
    """Three components, the middle one failing *bad*'s way; no builds."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / C.FILENAME).write_text(
        '[project]\nname = "proj"\nversion = "0.1.0"\n\n'
        '[good]\narg_type = "float"\n\n'
        '[bad]\narg_type = "float"\n\n'
        '[zgood]\narg_type = "float"\n',
        encoding="utf-8",
    )
    bdir = tmp_path / "bins"
    bdir.mkdir()
    scripts = {"good": _GOOD, "bad": _BAD[bad][0], "zgood": _GOOD}
    for comp, body in scripts.items():
        stem = f"bench_{comp}_core"
        exe = bdir / stem
        exe.write_text(body.format(stem=stem), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(B, "_build_bench_target", lambda *a, **k: True)
    monkeypatch.setattr(
        B, "_find_bench_binary", lambda _bd, comp: bdir / f"bench_{comp}_core"
    )
    monkeypatch.setattr(B, "_ensure_built", lambda *a, **k: None)
    monkeypatch.setattr(B, "_project_python", lambda _r: sys.executable)
    monkeypatch.setattr(B, "_commit_info", lambda: {})
    return root


@pytest.mark.parametrize("bad", sorted(_BAD))
class TestAFailedBinaryIsReported:
    def test_collect_names_it_and_keeps_the_others(
        self, tmp_path, monkeypatch, capsys, bad
    ):
        root = _project(tmp_path, monkeypatch, bad)
        failed: list[str] = []
        report = B._collect_c(
            root, root / "build", ["good", "bad", "zgood"], failed=failed
        )
        why = _BAD[bad][1]
        assert failed == [f"bench_bad_core ({why})"]
        # The component AFTER the failed one ran too, and no row of the
        # failed one was read, even where it wrote a whole JSON.
        assert [b["name"] for b in report["benchmarks"]] == [
            "good::step",
            "zgood::step",
        ]
        err = capsys.readouterr().err
        assert f"failed     bench_bad_core ({why})" in err
        assert f"    {_SAID[bad]}" in err, "its stderr is not quoted"

    def test_run_saves_the_rest_then_fails(
        self, tmp_path, monkeypatch, capsys, bad
    ):
        root = _project(tmp_path, monkeypatch, bad)
        with pytest.raises(SystemExit) as exc:
            B.run(root, do_python=False, tag="T")
        assert exc.value.code == 1
        snap = root / "benchmarks" / "history" / "T-c.json"
        assert snap.is_file(), "the benchmarks that passed were not saved"
        data = json.loads(snap.read_text(encoding="utf-8"))
        assert [b["name"] for b in data["benchmarks"]] == [
            "good::step",
            "zgood::step",
        ]
        # The committed snapshot says itself what is missing from it.
        assert data["failed"] == [f"bench_bad_core ({_BAD[bad][1]})"]
        err = capsys.readouterr().err
        assert "FAILED — 1 benchmark run(s) failed" in err
        assert "bench_bad_core" in err.split("FAILED —", 1)[1]


def test_a_good_binarys_stderr_still_reaches_ours(
    tmp_path, monkeypatch, capsys
):
    """Capturing stderr to quote it on failure must not swallow it when the
    run is good: a binary's own diagnostics (gh-806's "measures nothing")
    are on stderr."""
    root = _project(tmp_path, monkeypatch, "exit 1")
    report = B._collect_c(root, root / "build", ["good"])
    assert [b["name"] for b in report["benchmarks"]] == ["good::step"]
    err = capsys.readouterr().err
    assert "note from bench_good_core" in err
    assert "failed" not in err


def test_check_mode_fails_and_names_it(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, "exit 1")
    hist = root / "benchmarks" / "history"
    hist.mkdir(parents=True)
    base = {
        "benchmarks": [
            {"name": f"{c}::step", "stats": {"min": 1e-6, "mean": 1e-6}}
            for c in ("good", "bad", "zgood")
        ]
    }
    (hist / "A-c.json").write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        B.run(root, do_python=False, check=True, as_json=True)
    assert exc.value.code == 1
    # gh-1833: stdout is the document alone, so the whole of it parses.
    out = json.loads(capsys.readouterr().out)
    assert out["failed"] == ["bench_bad_core (exit 1)"]
    missing = [r["name"] for r in out["results"] if r["status"] == "missing"]
    assert missing == ["bad::step"]


def test_check_mode_fails_when_every_binary_failed(
    tmp_path, monkeypatch, capsys
):
    """With nothing collected there is no comparison and so no `missing`
    row: the failure alone has to fail the gate."""
    root = _project(tmp_path, monkeypatch, "exit 1")
    hist = root / "benchmarks" / "history"
    hist.mkdir(parents=True)
    (hist / "A-c.json").write_text(
        json.dumps({"benchmarks": [{"name": "bad::step", "stats": {}}]}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        B.run(root, components=["bad"], do_python=False, check=True)
    assert exc.value.code == 1
    assert "FAILED" in capsys.readouterr().err


def test_a_timeout_and_a_failure_are_both_named(tmp_path, monkeypatch, capsys):
    """One exit, two causes: neither message may hide the other."""
    root = _project(tmp_path, monkeypatch, "exit 1")
    slow = tmp_path / "bins" / "bench_zgood_core"
    slow.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        B.run(root, do_python=False, tag="T", timeout=1.0)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "TIMEOUT" in err and "bench_zgood_core" in err
    assert "FAILED" in err and "bench_bad_core (exit 1)" in err


def test_a_built_target_with_no_binary_is_a_failure(
    tmp_path, monkeypatch, capsys
):
    """The target built, so the benchmark exists; jm not finding it to run
    is not a reason to leave it out without a word."""
    root = _project(tmp_path, monkeypatch, "exit 1")
    bdir = tmp_path / "bins"
    monkeypatch.setattr(
        B,
        "_find_bench_binary",
        lambda _bd, comp: (
            None if comp == "bad" else bdir / f"bench_{comp}_core"
        ),
    )
    with pytest.raises(SystemExit) as exc:
        B.run(root, do_python=False, tag="T")
    assert exc.value.code == 1
    data = json.loads(
        (root / "benchmarks" / "history" / "T-c.json").read_text(
            encoding="utf-8"
        )
    )
    assert [b["name"] for b in data["benchmarks"]] == [
        "good::step",
        "zgood::step",
    ]
    assert data["failed"] == ["bench_bad_core (no binary found)"]
    err = capsys.readouterr().err
    assert "failed     bench_bad_core (no binary found)" in err
    assert "the target built, but no bench_bad_core is under" in err


# ── the Python side ──────────────────────────────────────────────────────────

#: pytest-benchmark files, each pinned to three rounds so a run costs no more
#: than pytest's start-up.
_PY_OK = """\
def test_ok(benchmark):
    benchmark.pedantic(lambda: sum(range(10)), rounds=3, iterations=1)
"""

#: The benchmarked function raises: pytest-benchmark records no timing.
_PY_RAISES = """\
def test_raises(benchmark):
    def f():
        raise RuntimeError("boom")

    benchmark.pedantic(f, rounds=3, iterations=1)
"""

#: The benchmark ran, then its test failed: pytest-benchmark KEEPS this
#: timing in its JSON, which is why a failed run's JSON is not read.
_PY_WRONG = """\
def test_wrong(benchmark):
    r = benchmark.pedantic(lambda: 1, rounds=3, iterations=1)
    assert r == 2
"""


def _py_project(tmp_path: Path, monkeypatch, files: dict) -> Path:
    """A manifest and a ``src/`` of benchmark files; no build, no C side."""
    # The interpreter is this test's own; without pytest-benchmark `jm bench`
    # reports "not installed" and every case below would pass on nothing.
    assert importlib.util.find_spec("pytest_benchmark") is not None, (
        "pytest-benchmark is missing from the test interpreter; declare it "
        "in PYTEST_DEPS (Makefile)"
    )
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / C.FILENAME).write_text(
        '[project]\nname = "proj"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    for name, body in files.items():
        (root / "src" / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(B, "_ensure_built", lambda *a, **k: None)
    monkeypatch.setattr(B, "_project_python", lambda _r: sys.executable)
    monkeypatch.setattr(B, "_commit_info", lambda: {})
    return root


def test_a_failed_python_run_is_reported_and_its_json_not_read(
    tmp_path, monkeypatch, capsys
):
    root = _py_project(
        tmp_path,
        monkeypatch,
        {
            "bench_a_ok.py": _PY_OK,
            "bench_b_raises.py": _PY_RAISES,
            "bench_c_wrong.py": _PY_WRONG,
        },
    )
    failed: list[str] = []
    report = B._run_python(root, sys.executable, timeout=120.0, failed=failed)
    assert failed == [
        "pytest src/bench_b_raises.py (exit 1)",
        "pytest src/bench_c_wrong.py (exit 1)",
    ]
    # test_wrong's timing was in its run's JSON; it must not be here.
    assert [b["name"] for b in report["benchmarks"]] == ["bench_a_ok::test_ok"]
    err = capsys.readouterr().err
    assert "failed     pytest src/bench_b_raises.py (exit 1)" in err


def test_run_saves_the_python_rest_then_fails(tmp_path, monkeypatch, capsys):
    root = _py_project(
        tmp_path,
        monkeypatch,
        {"bench_a_ok.py": _PY_OK, "bench_b_raises.py": _PY_RAISES},
    )
    with pytest.raises(SystemExit) as exc:
        B.run(root, do_c=False, tag="T")
    assert exc.value.code == 1
    data = json.loads(
        (root / "benchmarks" / "history" / "T.json").read_text(
            encoding="utf-8"
        )
    )
    assert [b["name"] for b in data["benchmarks"]] == ["bench_a_ok::test_ok"]
    assert data["failed"] == ["pytest src/bench_b_raises.py (exit 1)"]
    assert "FAILED — 1 benchmark run(s) failed" in capsys.readouterr().err


def test_python_check_mode_fails_and_names_it(tmp_path, monkeypatch, capsys):
    """No baseline at all, so the gate would otherwise skip and pass: the
    failure alone has to fail it."""
    root = _py_project(
        tmp_path, monkeypatch, {"bench_b_raises.py": _PY_RAISES}
    )
    with pytest.raises(SystemExit) as exc:
        B.run(root, do_c=False, check=True, as_json=True)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["failed"] == ["pytest src/bench_b_raises.py (exit 1)"]


def test_a_failure_inside_a_timed_out_file_is_named(
    tmp_path, monkeypatch, capsys
):
    """gh-2135 reruns a timed-out file one benchmark at a time. A benchmark
    that then fails is named by its node, beside the one that timed out."""
    slow_and_raises = (
        "import time\n\n\n"
        "def test_slow(benchmark):\n"
        "    benchmark.pedantic(time.sleep, args=(60,), rounds=1)\n\n\n"
        + _PY_RAISES
    )
    root = _py_project(
        tmp_path, monkeypatch, {"bench_mixed.py": slow_and_raises}
    )
    timed_out: list[str] = []
    failed: list[str] = []
    B._run_python(
        root, sys.executable, timeout=15.0, timed_out=timed_out, failed=failed
    )
    assert timed_out == ["pytest src/bench_mixed.py::test_slow"]
    assert failed == ["pytest src/bench_mixed.py::test_raises (exit 1)"]
