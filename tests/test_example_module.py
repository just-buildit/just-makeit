"""Unit tests for just_makeit._example."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from just_makeit import _example as E


class TestExamplesRoot:
    def test_returns_path_or_none(self):
        result = E._examples_root()
        assert result is None or result.is_dir()


class TestDiscover:
    def test_returns_list(self):
        names = E._discover()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_sorted(self):
        names = E._discover()
        assert names == sorted(names)

    def test_returns_empty_when_root_missing(self):
        with patch.object(E, "_examples_root", return_value=None):
            assert E._discover() == []


class TestFind:
    def test_returns_none_when_root_missing(self):
        with patch.object(E, "_examples_root", return_value=None):
            assert E._find("fir_filter") is None

    def test_returns_none_for_unknown_example(self, tmp_path):
        with patch.object(E, "_examples_root", return_value=tmp_path):
            assert E._find("nonexistent") is None

    def test_returns_path_for_valid_example(self, tmp_path):
        ex = tmp_path / "fir_filter"
        ex.mkdir()
        (ex / "test.py").write_text("")
        with patch.object(E, "_examples_root", return_value=tmp_path):
            assert E._find("fir_filter") == ex


class TestRun:
    def test_lists_examples_when_name_is_none(self, capsys):
        with patch.object(E, "_EXAMPLES", ["fir_filter", "nco"]):
            E.run(None)
        out = capsys.readouterr().out
        assert "fir_filter" in out
        assert "nco" in out

    def test_exits_for_unknown_example(self):
        with patch.object(E, "_find", return_value=None):
            with patch.object(E, "_EXAMPLES", []):
                with pytest.raises(SystemExit):
                    E.run("nonexistent")

    def test_runs_test_py_and_exits(self, tmp_path):
        ex = tmp_path / "myex"
        ex.mkdir()
        (ex / "test.py").write_text("")
        with patch.object(E, "_find", return_value=ex):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.__class__.returncode = 0
                mock_run.return_value.returncode = 0
                with pytest.raises(SystemExit) as exc:
                    E.run("myex")
                assert exc.value.code == 0
