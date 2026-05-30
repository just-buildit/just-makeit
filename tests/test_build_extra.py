"""Additional unit tests for just_makeit._build — pushing toward 100%."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from just_makeit import _build as B


class TestCmakeConfigure:
    def test_runs_cmake_command(self, tmp_path):
        with patch.object(B, "_require", return_value="cmake"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                B._cmake_configure(tmp_path, tmp_path / "build")
                cmd = mock_run.call_args[0][0]
                assert "cmake" in cmd
                assert "-B" in cmd
                assert "-S" in cmd
                assert any("Release" in a for a in cmd)

    def test_exits_nonzero_on_failure(self, tmp_path):
        with patch.object(B, "_require", return_value="cmake"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                with pytest.raises(SystemExit):
                    B._cmake_configure(tmp_path, tmp_path / "build")

    def test_passes_python_executable(self, tmp_path):
        with patch.object(B, "_require", return_value="cmake"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                B._cmake_configure(tmp_path, tmp_path / "build")
                cmd = mock_run.call_args[0][0]
                assert any("Python3_EXECUTABLE" in a for a in cmd)


class TestCmakeBuild:
    def test_runs_cmake_build(self, tmp_path):
        with patch.object(B, "_require", return_value="cmake"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                B._cmake_build(tmp_path, tmp_path / "build")
                cmd = mock_run.call_args[0][0]
                assert "--build" in cmd

    def test_exits_on_failure(self, tmp_path):
        with patch.object(B, "_require", return_value="cmake"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=2)
                with pytest.raises(SystemExit):
                    B._cmake_build(tmp_path, tmp_path / "build")

    def test_passes_parallel_flag(self, tmp_path):
        with patch.object(B, "_require", return_value="cmake"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                B._cmake_build(tmp_path, tmp_path / "build")
                cmd = mock_run.call_args[0][0]
                assert "--parallel" in cmd


class TestCmdBuild:
    def test_no_rest_creates_dist_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch("just_buildit.build_wheel", return_value="pkg.whl"):
                B.cmd_build([])
        assert (tmp_path / "dist").is_dir()

    def test_custom_dest_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        custom = tmp_path / "out"
        with patch.object(B, "_ensure_built"):
            with patch("just_buildit.build_wheel", return_value="pkg.whl"):
                B.cmd_build([str(custom)])
        assert custom.is_dir()

    def test_just_buildit_missing_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch.dict(sys.modules, {"just_buildit": None}):
                with pytest.raises(SystemExit):
                    B.cmd_build([])

    def test_calls_build_wheel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch(
                "just_buildit.build_wheel", return_value="pkg.whl"
            ) as mock:
                B.cmd_build([])
                mock.assert_called_once()


class TestCmdTest:
    def test_calls_ctest_when_both_pass(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch.object(B, "_require", return_value="ctest"):
                with patch.object(B, "_run_python_tests", return_value=True):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        B.cmd_test([])
                        cmd = mock_run.call_args[0][0]
                        assert "ctest" in cmd

    def test_exits_when_ctest_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch.object(B, "_require", return_value="ctest"):
                with patch.object(B, "_run_python_tests", return_value=True):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=1)
                        with pytest.raises(SystemExit):
                            B.cmd_test([])

    def test_exits_when_pytest_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch.object(B, "_require", return_value="ctest"):
                with patch.object(B, "_run_python_tests", return_value=False):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        with pytest.raises(SystemExit):
                            B.cmd_test([])

    def test_passes_extra_args_to_python_tests(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(B, "_ensure_built"):
            with patch.object(B, "_require", return_value="ctest"):
                with patch.object(
                    B, "_run_python_tests", return_value=True
                ) as mock_py:
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        B.cmd_test(["-k", "myfunc"])
                        mock_py.assert_called_once_with(
                            Path.cwd(), ["-k", "myfunc"]
                        )


class TestCmdDryRunBranches:
    def test_native_src_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        with patch("shutil.which", return_value=None):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "native/src/ not found" in out

    def test_python_files_listed(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text("")
        with patch("shutil.which", return_value=None):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "mod.py" in out

    def test_pyi_files_listed(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "mod.pyi").write_text("")
        with patch("shutil.which", return_value=None):
            B.cmd_dry_run()
        out = capsys.readouterr().out
        assert "mod.pyi" in out
