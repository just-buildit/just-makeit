"""Additional unit tests for just_makeit._bench — pure helpers."""

import json
from unittest.mock import patch


from just_makeit import _bench as B


class TestPickUnit:
    def test_nanoseconds(self):
        assert B._pick_unit([1e-9, 2e-9]) == "ns"

    def test_microseconds(self):
        assert B._pick_unit([1e-6, 2e-6]) == "μs"

    def test_milliseconds(self):
        assert B._pick_unit([1e-3, 2e-3]) == "ms"

    def test_seconds(self):
        assert B._pick_unit([1.5, 2.0]) == "s"

    def test_empty_defaults_us(self):
        assert B._pick_unit([]) == "μs"


class TestFmtTime:
    def test_nanoseconds(self):
        assert "ns" in B._fmt_time(1e-9, "ns")

    def test_microseconds(self):
        assert "μs" in B._fmt_time(1e-6, "μs")

    def test_milliseconds(self):
        assert "ms" in B._fmt_time(1e-3, "ms")

    def test_seconds(self):
        result = B._fmt_time(1.5, "s")
        assert "s" in result
        assert "1.5" in result


class TestFmtOps:
    def test_giga(self):
        result = B._fmt_ops(2e9)
        assert "GSa/s" in result

    def test_mega(self):
        result = B._fmt_ops(2e6)
        assert "MSa/s" in result

    def test_kilo(self):
        result = B._fmt_ops(2e3)
        assert "kSa/s" in result


class TestTrim:
    def test_removes_data_field(self):
        report = {
            "benchmarks": [
                {"name": "test", "stats": {"min": 1e-6, "data": [1, 2, 3]}}
            ]
        }
        result = B._trim(report)
        assert "data" not in result["benchmarks"][0]["stats"]

    def test_removes_runtimes_field(self):
        report = {
            "benchmarks": [
                {"name": "test", "stats": {"min": 1e-6, "runtimes": [1, 2, 3]}}
            ]
        }
        result = B._trim(report)
        assert "runtimes" not in result["benchmarks"][0]["stats"]

    def test_keeps_summary_stats(self):
        report = {
            "benchmarks": [
                {
                    "name": "test",
                    "stats": {"min": 1e-6, "mean": 2e-6, "data": []},
                }
            ]
        }
        result = B._trim(report)
        assert result["benchmarks"][0]["stats"]["min"] == 1e-6
        assert result["benchmarks"][0]["stats"]["mean"] == 2e-6

    def test_empty_benchmarks(self):
        report = {"benchmarks": []}
        assert B._trim(report) == {"benchmarks": []}

    def test_no_benchmarks_key(self):
        assert B._trim({}) == {}


class TestHistoryDir:
    def test_returns_benchmarks_history(self, tmp_path):
        result = B._history_dir(tmp_path)
        assert result == tmp_path / "benchmarks" / "history"


class TestTag:
    def test_returns_string(self):
        tag = B._tag()
        assert isinstance(tag, str)
        assert len(tag) > 0

    def test_ends_with_z(self):
        assert B._tag().endswith("Z")


class TestSnapshotPath:
    def test_python_snapshot(self, tmp_path):
        p = B._snapshot_path(tmp_path, "20240101T000000Z", is_c=False)
        assert p == tmp_path / "20240101T000000Z.json"

    def test_c_snapshot(self, tmp_path):
        p = B._snapshot_path(tmp_path, "20240101T000000Z", is_c=True)
        assert p == tmp_path / "20240101T000000Z-c.json"


class TestPrevSnapshot:
    def test_returns_none_when_no_history_dir(self, tmp_path):
        result = B._prev_snapshot(
            tmp_path / "nonexistent", "20240101T000000Z", False
        )
        assert result is None

    def test_returns_none_when_empty_dir(self, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        result = B._prev_snapshot(tmp_path, "20240101T000000Z", False)
        assert result is None

    def test_returns_most_recent_before_tag(self, tmp_path):
        old = {"benchmarks": [{"name": "old"}]}
        (tmp_path / "20230101T000000Z.json").write_text(json.dumps(old))
        result = B._prev_snapshot(tmp_path, "20240101T000000Z", is_c=False)
        assert result is not None
        assert result["benchmarks"][0]["name"] == "old"

    def test_skips_newer_snapshots(self, tmp_path):
        data = {"benchmarks": []}
        (tmp_path / "20250101T000000Z.json").write_text(json.dumps(data))
        result = B._prev_snapshot(tmp_path, "20240101T000000Z", is_c=False)
        assert result is None

    def test_c_suffix_not_mixed_with_python(self, tmp_path):
        c_data = {"benchmarks": [{"name": "c"}]}
        (tmp_path / "20230101T000000Z-c.json").write_text(json.dumps(c_data))
        result = B._prev_snapshot(tmp_path, "20240101T000000Z", is_c=False)
        assert result is None

    def test_c_snapshot_found(self, tmp_path):
        c_data = {"benchmarks": [{"name": "c"}]}
        (tmp_path / "20230101T000000Z-c.json").write_text(json.dumps(c_data))
        result = B._prev_snapshot(tmp_path, "20240101T000000Z", is_c=True)
        assert result is not None


class TestSaveSnapshot:
    def test_creates_file(self, tmp_path):
        hdir = tmp_path / "history"
        report = {"benchmarks": []}
        B._save_snapshot(
            tmp_path, hdir, "20240101T000000Z", report, is_c=False
        )
        assert (hdir / "20240101T000000Z.json").exists()

    def test_creates_c_file(self, tmp_path):
        hdir = tmp_path / "history"
        report = {"benchmarks": []}
        B._save_snapshot(tmp_path, hdir, "20240101T000000Z", report, is_c=True)
        assert (hdir / "20240101T000000Z-c.json").exists()

    def test_file_is_valid_json(self, tmp_path):
        hdir = tmp_path / "history"
        report = {"benchmarks": [{"name": "x"}]}
        B._save_snapshot(
            tmp_path, hdir, "20240101T000000Z", report, is_c=False
        )
        loaded = json.loads((hdir / "20240101T000000Z.json").read_text())
        assert loaded["benchmarks"][0]["name"] == "x"


class TestHasPytestBenchmark:
    def test_true_when_importable(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0})()
            assert B._has_pytest_benchmark("/usr/bin/python") is True

    def test_false_when_not_importable(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 1})()
            assert B._has_pytest_benchmark("/usr/bin/python") is False


class TestDisplayTable:
    def _make_report(self, names):
        return {
            "benchmarks": [
                {
                    "name": n,
                    "stats": {
                        "min": 1e-6,
                        "max": 2e-6,
                        "mean": 1.5e-6,
                        "stddev": 0.1e-6,
                        "median": 1.5e-6,
                        "ops": 1e6,
                    },
                }
                for n in names
            ]
        }

    def test_no_benchmarks_prints_message(self, capsys):
        B._display_table("C", {"benchmarks": []}, None)
        out = capsys.readouterr().out
        assert "no benchmarks" in out

    def test_prints_table_with_names(self, capsys):
        report = self._make_report(["test_fn"])
        B._display_table("C", report, None)
        out = capsys.readouterr().out
        assert "test_fn" in out

    def test_delta_column_with_prev(self, capsys):
        report = self._make_report(["test_fn"])
        prev = self._make_report(["test_fn"])
        B._display_table("Python", report, prev)
        out = capsys.readouterr().out
        assert "%" in out

    def test_machine_info_in_header(self, capsys):
        report = self._make_report(["fn"])
        report["machine_info"] = {"system": "Linux", "node": "myhost"}
        B._display_table("C", report, None)
        out = capsys.readouterr().out
        assert "Linux" in out or "myhost" in out
