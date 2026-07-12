"""gh-141: `jm bench --check` regression gate.

The gate logic (`_compare_reports`) is a pure function over the snapshot dicts
jm already writes, so it is tested directly — no build required. `_baseline_
snapshot` picks the baseline (named tag, else latest; C and Python kept
separate).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._bench import (
    _compare_reports,
    _baseline_snapshot,
    _qualify_python_names,
)


def _report(**means):
    """Build a snapshot report: name -> mean-seconds."""
    return {
        "benchmarks": [
            {"name": n, "stats": {"mean": m}} for n, m in means.items()
        ]
    }


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_regression_detected_above_threshold():
    base = _report(fir=1e-3)
    cur = _report(fir=1.2e-3)  # +20%
    rows = _by_name(_compare_reports(cur, base, threshold=0.10))
    assert rows["fir"]["status"] == "regressed"
    assert abs(rows["fir"]["delta_pct"] - 20.0) < 1e-6


def test_within_threshold_is_ok():
    base = _report(fir=1e-3)
    cur = _report(fir=1.05e-3)  # +5% < 10%
    rows = _by_name(_compare_reports(cur, base, threshold=0.10))
    assert rows["fir"]["status"] == "ok"


def test_speedup_is_ok():
    rows = _by_name(
        _compare_reports(
            _report(fir=0.5e-3), _report(fir=1e-3), threshold=0.10
        )
    )
    assert rows["fir"]["status"] == "ok"
    assert rows["fir"]["delta_pct"] < 0


def test_new_benchmark_has_no_baseline():
    rows = _by_name(
        _compare_reports(_report(neo=1e-3), _report(fir=1e-3), threshold=0.10)
    )
    assert rows["neo"]["status"] == "new"
    assert rows["neo"]["baseline_ns"] is None


def test_allow_exempts_from_gate():
    base = _report(fir=1e-3)
    cur = _report(fir=2e-3)  # +100%, but allowed
    rows = _by_name(_compare_reports(cur, base, threshold=0.10, allow={"fir"}))
    assert rows["fir"]["status"] == "allowed"


def test_noise_floor_skips_subthreshold_benchmarks():
    # baseline below the 500 ns floor: jitter, not gated even at +100%.
    base = _report(tiny=1e-7)  # 100 ns
    cur = _report(tiny=2e-7)
    rows = _by_name(_compare_reports(cur, base, threshold=0.10))
    assert rows["tiny"]["status"] == "below_floor"


def test_baseline_snapshot_picks_latest(tmp_path):
    hdir = tmp_path / "benchmarks" / "history"
    hdir.mkdir(parents=True)
    (hdir / "20260101T000000Z.json").write_text(json.dumps(_report(fir=2e-3)))
    (hdir / "20260201T000000Z.json").write_text(json.dumps(_report(fir=1e-3)))
    base = _baseline_snapshot(hdir, is_c=False)
    assert base["benchmarks"][0]["stats"]["mean"] == 1e-3  # the newer one


def test_baseline_snapshot_named_tag(tmp_path):
    hdir = tmp_path / "benchmarks" / "history"
    hdir.mkdir(parents=True)
    (hdir / "v1.json").write_text(json.dumps(_report(fir=2e-3)))
    (hdir / "v2.json").write_text(json.dumps(_report(fir=1e-3)))
    base = _baseline_snapshot(hdir, is_c=False, tag="v1")
    assert base["benchmarks"][0]["stats"]["mean"] == 2e-3


def test_baseline_snapshot_separates_c_and_python(tmp_path):
    hdir = tmp_path / "benchmarks" / "history"
    hdir.mkdir(parents=True)
    (hdir / "20260101T000000Z.json").write_text(json.dumps(_report(py=1e-3)))
    (hdir / "20260101T000000Z-c.json").write_text(json.dumps(_report(c=1e-3)))
    py = _baseline_snapshot(hdir, is_c=False)
    cc = _baseline_snapshot(hdir, is_c=True)
    assert py["benchmarks"][0]["name"] == "py"
    assert cc["benchmarks"][0]["name"] == "c"


def test_baseline_snapshot_missing_returns_none(tmp_path):
    assert _baseline_snapshot(tmp_path / "nope", is_c=False) is None


def test_fullname_disambiguates_duplicate_names():
    """gh-141 follow-up: benches with the same `name` in different modules must
    match by `fullname`, not collide. Here both are unchanged → all OK; with
    name-only keying the baseline dict would keep one entry and cross-compare,
    producing a bogus regression."""
    rep = {
        "benchmarks": [
            {
                "name": "execute_64k",
                "fullname": "a.py::execute_64k",
                "stats": {"min": 1e-3},
            },
            {
                "name": "execute_64k",
                "fullname": "b.py::execute_64k",
                "stats": {"min": 1e-6},
            },
        ]
    }
    rows = _compare_reports(rep, rep, threshold=0.10)
    assert {r["id"] for r in rows} == {
        "a.py::execute_64k",
        "b.py::execute_64k",
    }
    assert not [r for r in rows if r["status"] == "regressed"]


def test_qualify_python_names_disambiguates_display():
    """gh-141 fixed `_bench_key`'s baseline<->current matching, but the
    *display* table (`_display_table`) still prints and Δ-keys on the bare
    `name` -- unchanged, it silently collides two components' same-named
    benches in `jm bench`'s printed summary. `_qualify_python_names` prefixes
    `name` from `fullname`'s file stem (mirrors `_collect_c`'s `f"{comp}::…"`
    convention), mutating in place before the report reaches `_display_table`
    or gets written to a snapshot."""
    data = {
        "benchmarks": [
            {
                "name": "test_bench_steps_64k",
                "fullname": "src/doppler/cvt/benchmarks/"
                "bench_i16_to_f32.py::test_bench_steps_64k[obj0]",
            },
            {
                "name": "test_bench_steps_64k",
                "fullname": "src/doppler/resample/benchmarks/"
                "bench_farrow.py::test_bench_steps_64k[obj0]",
            },
        ]
    }
    _qualify_python_names(data)
    names = {b["name"] for b in data["benchmarks"]}
    assert names == {
        "bench_i16_to_f32::test_bench_steps_64k",
        "bench_farrow::test_bench_steps_64k",
    }


def test_qualify_python_names_skips_entries_without_fullname():
    """A malformed/legacy entry with no `::` in `fullname` (or none at all)
    is left as-is rather than raising."""
    data = {"benchmarks": [{"name": "bare"}, {"name": "x", "fullname": "y"}]}
    _qualify_python_names(data)  # must not raise
    assert [b["name"] for b in data["benchmarks"]] == ["bare", "x"]


def test_uses_min_metric_when_present():
    """Comparison uses `min` (stable) over `mean` (noisy) when both exist."""
    base = {
        "benchmarks": [{"name": "f", "stats": {"min": 1e-3, "mean": 1e-3}}]
    }
    # mean spikes 5x (noise) but min holds → no regression.
    cur = {
        "benchmarks": [{"name": "f", "stats": {"min": 1.02e-3, "mean": 5e-3}}]
    }
    rows = _by_name(_compare_reports(cur, base, threshold=0.10))
    assert rows["f"]["status"] == "ok"
