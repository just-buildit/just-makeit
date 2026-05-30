"""Unit tests for just_makeit._bench."""

import sys
from unittest.mock import patch

import pytest

from just_makeit import _bench as B


class TestProjectPython:
    def test_falls_back_to_sys_executable_when_no_venv(self, tmp_path):
        assert B._project_python(tmp_path) == sys.executable

    def test_finds_venv_python(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text("#!/usr/bin/env python3")
        py.chmod(0o755)
        assert B._project_python(tmp_path) == str(py)


class TestFindBenchBinary:
    def test_returns_none_when_not_found(self, tmp_path):
        assert B._find_bench_binary(tmp_path, "fir") is None

    def test_finds_binary_at_canonical_path(self, tmp_path):
        stem = "bench_fir_core"
        (tmp_path / stem).write_text("")
        (tmp_path / stem).chmod(0o755)
        result = B._find_bench_binary(tmp_path, "fir")
        assert result == tmp_path / stem

    def test_finds_binary_via_rglob(self, tmp_path):
        nested = tmp_path / "some" / "deep" / "dir"
        nested.mkdir(parents=True)
        binary = nested / "bench_nco_core"
        binary.write_text("")
        result = B._find_bench_binary(tmp_path, "nco")
        assert result == binary


class TestMachineInfo:
    def test_returns_dict_with_expected_keys(self):
        info = B._machine_info()
        assert "node" in info
        assert "system" in info
        assert "machine" in info
        assert "cpu" in info
        assert "count" in info["cpu"]


class TestCommitInfo:
    def test_returns_dict_inside_repo(self):
        result = B._commit_info()
        if result:
            assert "id" in result
            assert "branch" in result
            assert "dirty" in result

    def test_returns_empty_outside_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("subprocess.check_output", side_effect=OSError):
            result = B._commit_info()
        assert result == {}


class TestRequire:
    def test_exits_when_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                B._require("cmake")

    def test_returns_path_when_found(self):
        with patch("shutil.which", return_value="/usr/bin/cmake"):
            assert B._require("cmake") == "/usr/bin/cmake"
