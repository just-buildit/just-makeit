"""Unit tests for just_makeit._build."""

from unittest.mock import MagicMock, patch

import pytest

from just_makeit import _build as B


class TestRequire:
    def test_returns_path_when_found(self, tmp_path):
        exe = tmp_path / "cmake"
        exe.touch(mode=0o755)
        with patch("shutil.which", return_value=str(exe)):
            assert B._require("cmake") == str(exe)

    def test_exits_when_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                B._require("cmake")


class TestHasPytest:
    def test_true_when_pytest_importable(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert B._has_pytest() is True

    def test_false_when_pytest_missing(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert B._has_pytest() is False


class TestRunPythonTests:
    def test_uses_pytest_when_available(self, tmp_path):
        with patch.object(B, "_has_pytest", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = B._run_python_tests(tmp_path, [])
                assert result is True
                cmd = mock_run.call_args[0][0]
                assert "-m" in cmd and "pytest" in cmd

    def test_falls_back_to_unittest(self, tmp_path):
        with patch.object(B, "_has_pytest", return_value=False):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                B._run_python_tests(tmp_path, [])
                cmd = mock_run.call_args[0][0]
                assert "unittest" in cmd

    def test_returns_false_on_failure(self, tmp_path):
        with patch.object(B, "_has_pytest", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                assert B._run_python_tests(tmp_path, []) is False


class TestCmdDryRun:
    def test_no_pyproject_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            B.cmd_dry_run()

    def test_lists_c_sources(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        src = tmp_path / "native" / "src" / "foo"
        src.mkdir(parents=True)
        (src / "foo.c").write_text("int main(){}")
        with patch("shutil.which", return_value=None):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "foo.c" in out

    def test_no_c_sources_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        (tmp_path / "native" / "src").mkdir(parents=True)
        with patch("shutil.which", return_value=None):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "(none)" in out

    def test_cmake_command_shown_when_available(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        with patch("shutil.which", return_value="/usr/bin/cmake"):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "cmake" in out and "configure" in out

    def test_cmake_not_found_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        with patch("shutil.which", return_value=None):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "not found" in out


class TestEnsureBuilt:
    def test_configures_when_no_cache(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        with patch.object(B, "_cmake_configure") as mock_cfg:
            with patch.object(B, "_cmake_build") as mock_bld:
                B._ensure_built(tmp_path, build_dir)
                mock_cfg.assert_called_once()
                mock_bld.assert_called_once()

    def test_skips_configure_when_cache_exists(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "CMakeCache.txt").write_text("")
        with patch.object(B, "_cmake_configure") as mock_cfg:
            with patch.object(B, "_cmake_build") as mock_bld:
                B._ensure_built(tmp_path, build_dir)
                mock_cfg.assert_not_called()
                mock_bld.assert_called_once()
