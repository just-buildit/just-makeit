"""gh-1029: `jm bench --check` must be able to fail on a benchmark that went.

`_compare_reports` returned "one record per **current** benchmark" — its own
docstring said so. A benchmark the baseline carried and the run did not
produce therefore emitted **no record at all**: it could not be `regressed`,
could not be `new`, and simply stopped being compared. The gate's coverage was
whatever this run happened to produce, so shrinking that set always looked
like success.

Every path there is silent by construction. `_collect_c` skips a binary it
cannot find, one that writes no JSON, and one whose JSON does not parse;
gh-1023 added a deliberate `skip` for a discovered target that does not build;
and renaming a kernel retires the old name while reporting the new one as
`new`, which never fails. It is the same shape gh-1033 fixes in `_hollow` —
an answer the function could not express, read as the good answer.

The fix is the **union** of the two key sets, plus a `missing` status that
FAILS. That last part is the decision gh-1029 leaves open, and it is settled
the way this repo settles it: a gate that passes on nothing is
indistinguishable from a gate passing. A deletion you meant is a deliberate
act and carries `--allow`, which already exists.

The registration-free half is `test_the_output_covers_the_union`: it asserts a
set identity over the comparison's keys rather than checking for the string
"missing", so any future status, any future early `continue`, and any future
reordering that drops a key fails it — with nothing to remember to update.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _bench as B  # noqa: E402


def _snap(*entries) -> dict:
    """A snapshot in the shape `_compare_reports` reads.

    ``entries`` are ``(name, seconds)``. The C side has no ``fullname``, so
    ``name`` is the key — which is also the case the bug was found in.
    """
    return {
        "benchmarks": [
            {"name": n, "stats": {"min": s, "mean": s}} for n, s in entries
        ]
    }


def _by_id(rows) -> dict:
    return {r["id"]: r for r in rows}


class TestAVanishedBenchmarkIsReported:
    def test_it_produces_a_record(self):
        """The narrowest statement of the bug: there was no record."""
        rows = B._compare_reports(
            _snap(("fir::step", 1e-3)),
            _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
            0.10,
        )
        assert "fir::reset" in _by_id(rows)

    def test_its_status_is_missing(self):
        rows = _by_id(
            B._compare_reports(
                _snap(("fir::step", 1e-3)),
                _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
                0.10,
            )
        )
        gone = rows["fir::reset"]
        assert gone["status"] == "missing"
        # The baseline number is what the reader needs to judge the loss;
        # there is no current number, and saying 0 would be a measurement
        # nobody took.
        assert gone["current_ns"] is None
        assert gone["delta_pct"] is None
        assert gone["baseline_ns"] == pytest.approx(2e-3 * 1e9)

    def test_a_survivor_is_unaffected(self):
        rows = _by_id(
            B._compare_reports(
                _snap(("fir::step", 1e-3)),
                _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
                0.10,
            )
        )
        assert rows["fir::step"]["status"] == "ok"

    def test_a_rename_reports_both_halves(self):
        """The path a reviewer is most likely to meet.

        Renaming a kernel retired the old name from the comparison entirely
        and reported the new one as `new` — and `new` never fails, so a rename
        that also broke the kernel read as a clean run.
        """
        rows = _by_id(
            B._compare_reports(
                _snap(("fir::step_v2", 1e-3)),
                _snap(("fir::step", 1e-3)),
                0.10,
            )
        )
        assert rows["fir::step_v2"]["status"] == "new"
        assert rows["fir::step"]["status"] == "missing"

    def test_an_empty_current_report_does_not_read_as_clean(self):
        """The whole harness failing to run is the extreme of the same bug.

        `_collect_c` returning nothing usable produced an empty comparison,
        and an empty comparison had no regressions in it.
        """
        rows = B._compare_reports(
            None, _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)), 0.10
        )
        assert {r["status"] for r in rows} == {"missing"}
        assert len(rows) == 2


class TestAllowIsTheEscape:
    """A deliberate deletion is a deliberate act, so it can carry a flag.

    No new flag: `--allow` is the same escape a deliberately slower benchmark
    uses, which is the point — a second mechanism for "I meant this" is a
    second mechanism to forget.
    """

    def test_an_allowed_name_does_not_fail(self):
        rows = _by_id(
            B._compare_reports(
                _snap(("fir::step", 1e-3)),
                _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
                0.10,
                allow={"fir::reset"},
            )
        )
        assert rows["fir::reset"]["status"] == "allowed"

    def test_it_is_still_reported(self):
        """`allowed` means "never fails", not "never mentioned" — the record
        stays in the output so a stale exemption is visible."""
        rows = B._compare_reports(
            _snap(),
            _snap(("fir::reset", 2e-3)),
            0.10,
            allow={"fir::reset"},
        )
        assert len(rows) == 1


class TestTheNoiseFloorDoesNotApply:
    """`below_floor` answers "is this timing trustworthy?".

    An absent benchmark has no timing to distrust, and a fast kernel
    disappearing loses exactly as much coverage as a slow one. Asserted
    because the tempting implementation reuses the timing branch wholesale.
    """

    def test_a_sub_floor_baseline_still_reports_missing(self):
        rows = _by_id(
            B._compare_reports(_snap(), _snap(("fir::tiny", 1e-9)), 0.10)
        )
        assert rows["fir::tiny"]["status"] == "missing"


class TestTheOutputCoversTheUnion:
    """The registration-free gate.

    Nothing here mentions `missing`. The property is that the comparison's key
    set IS the union of the two snapshots' key sets — so a record dropped for
    any reason, by any future branch, fails this with no list to maintain.
    That matters because the bug was not a wrong status; it was a row that was
    never emitted, and a test that looks for a status cannot see an absence.
    """

    CASES = [
        ("both empty", _snap(), _snap()),
        ("only current", _snap(("a", 1e-3)), _snap()),
        ("only baseline", _snap(), _snap(("a", 1e-3))),
        ("disjoint", _snap(("a", 1e-3)), _snap(("b", 1e-3))),
        (
            "overlapping",
            _snap(("a", 1e-3), ("b", 1e-3)),
            _snap(("b", 2e-3), ("c", 1e-9)),
        ),
    ]

    @pytest.mark.parametrize(
        "label,cur,base", CASES, ids=[c[0] for c in CASES]
    )
    def test_the_output_covers_the_union(self, label, cur, base):
        keys = {B._bench_key(b) for b in cur["benchmarks"]} | {
            B._bench_key(b) for b in base["benchmarks"]
        }
        rows = B._compare_reports(cur, base, 0.10)
        assert {r["id"] for r in rows} == keys
        # One record per key, not two: a union built by concatenation without
        # subtracting what was already emitted double-counts the overlap, and
        # a duplicated row would make the summary counts wrong in the
        # direction that reads as more coverage.
        assert len(rows) == len(keys)

    def test_the_order_is_deterministic(self):
        """Two runs of the same inputs must print the same thing.

        The baseline half is appended from a set, so without an explicit sort
        the output order would follow hash iteration and a diff of two
        `--json` runs would show spurious churn.
        """
        cur, base = self.CASES[-1][1], self.CASES[-1][2]
        first = [r["id"] for r in B._compare_reports(cur, base, 0.10)]
        second = [r["id"] for r in B._compare_reports(cur, base, 0.10)]
        assert first == second


def _check(tmp_path, monkeypatch, current, baseline, allow=(), as_json=False):
    """Drive `_run_check` over synthetic snapshots and capture its exit."""
    monkeypatch.setattr(B, "_collect_c", lambda *a, **k: current, raising=True)
    monkeypatch.setattr(
        B, "_baseline_snapshot", lambda *a, **k: baseline, raising=True
    )
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        try:
            B._run_check(
                tmp_path,
                tmp_path / "build",
                ["fir"],
                "python3",
                do_c=True,
                do_python=False,
                hdir=tmp_path / "h",
                threshold=0.10,
                baseline=None,
                as_json=as_json,
                allow=set(allow),
            )
        except SystemExit as e:
            code = e.code
    return code, buf.getvalue()


class TestTheGateActuallyFails:
    """The pure comparison being right is only half of it.

    gh-1029's title is about the *gate*, and a `missing` record that reaches
    the report but not the exit code would leave CI exactly as green as
    before.
    """

    def test_a_vanished_benchmark_exits_nonzero(self, tmp_path, monkeypatch):
        code, out = _check(
            tmp_path,
            monkeypatch,
            _snap(("fir::step", 1e-3)),
            _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
        )
        assert code == 1
        assert "[missing] C:fir::reset" in out

    def test_it_does_not_claim_OK(self, tmp_path, monkeypatch):
        """The summary line is what a reader takes away.

        "OK — no regression" over a run that lost a kernel is the sentence
        this issue is about, and it is printed whether or not anyone reads
        the exit code.
        """
        _, out = _check(
            tmp_path,
            monkeypatch,
            _snap(("fir::step", 1e-3)),
            _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
        )
        assert "OK — no regression" not in out
        assert "MISSING" in out
        # Point at the fix, and name the escape — a gate whose message does
        # not say how to clear it gets cleared by disabling the gate.
        assert "--allow" in out

    def test_an_allowed_deletion_passes(self, tmp_path, monkeypatch):
        code, out = _check(
            tmp_path,
            monkeypatch,
            _snap(("fir::step", 1e-3)),
            _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
            allow=("fir::reset",),
        )
        assert code == 0
        assert "OK — no regression" in out

    def test_a_clean_run_still_passes(self, tmp_path, monkeypatch):
        code, out = _check(
            tmp_path,
            monkeypatch,
            _snap(("fir::step", 1e-3)),
            _snap(("fir::step", 1e-3)),
        )
        assert code == 0
        assert "OK — no regression" in out

    def test_a_regression_still_fails(self, tmp_path, monkeypatch):
        """The behaviour that already worked, kept honest.

        The reporting branch grew a second failure kind; a rewrite that
        reports `missing` and forgets `regressed` would pass every test
        above.
        """
        code, out = _check(
            tmp_path,
            monkeypatch,
            _snap(("fir::step", 5e-3)),
            _snap(("fir::step", 1e-3)),
        )
        assert code == 1
        assert "REGRESSION" in out

    def test_json_carries_the_missing_record(self, tmp_path, monkeypatch):
        """`--json` is the CI consumer's view and must not be the quieter
        one — a gate that fires for a human and not for `--json` is found
        the hard way."""
        code, out = _check(
            tmp_path,
            monkeypatch,
            _snap(("fir::step", 1e-3)),
            _snap(("fir::step", 1e-3), ("fir::reset", 2e-3)),
            as_json=True,
        )
        assert code == 1
        payload = json.loads(out)
        statuses = {r["id"]: r["status"] for r in payload["results"]}
        assert statuses["fir::reset"] == "missing"
