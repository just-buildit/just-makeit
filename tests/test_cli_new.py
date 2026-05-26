"""Unit tests for just_makeit._cli_new."""

import pytest
from unittest.mock import patch


def _run(args):
    from just_makeit import _cli_new
    _cli_new.run(args)


class TestCliNew:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_object_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--object"])

    def test_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--module"])

    def test_build_system_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--build-system"])

    def test_build_system_invalid_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--build-system", "ninja"])

    def test_arg_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--arg-type"])

    def test_return_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--return-type"])

    def test_return_type_array_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--return-type", "float[]"])

    def test_arg_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--arg-type", "notatype"])

    def test_arg_type_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--arg-type", "notatype[]"])

    def test_return_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--return-type", "notatype"])

    def test_unknown_arg_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--unknown"])

    def test_flag_like_unknown_exits(self):
        with pytest.raises(SystemExit):
            _run(["myproj", "--bogus-flag"])

    def test_minimal_call(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj"])
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == "myproj"

    def test_object_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--object", "fir"])
            args, _ = mock_run.call_args
            assert args[2] == ["fir"]

    def test_multiple_objects(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--object", "fir", "--object", "biquad"])
            args, _ = mock_run.call_args
            assert set(args[2]) == {"fir", "biquad"}

    def test_module_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--module", "signal"])
            _, kwargs = mock_run.call_args
            assert kwargs["modules"] == ["signal"]

    def test_build_system_cmake(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--build-system", "cmake"])
            _, kwargs = mock_run.call_args
            assert kwargs["build_system"] == "cmake"

    def test_build_system_make(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--build-system", "make"])
            _, kwargs = mock_run.call_args
            assert kwargs["build_system"] == "make"

    def test_basic_flag_deprecated(self, capsys):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--basic"])
            _, kwargs = mock_run.call_args
            assert kwargs["build_system"] == "make"
        assert "deprecated" in capsys.readouterr().err

    def test_perf_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--perf"])
            _, kwargs = mock_run.call_args
            assert kwargs["perf"] is True

    def test_pytest_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--pytest"])
            _, kwargs = mock_run.call_args
            assert kwargs["pytest_"] is True

    def test_pytest_benchmark_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--pytest-benchmark"])
            _, kwargs = mock_run.call_args
            assert kwargs["pytest_benchmark_"] is True

    def test_mutable_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--mutable"])
            _, kwargs = mock_run.call_args
            assert kwargs["mutable"] is True

    def test_arg_type_float(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--arg-type", "float"])
            _, kwargs = mock_run.call_args
            assert kwargs["arg_type"] == "float"

    def test_arg_type_array(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--arg-type", "float[]"])
            _, kwargs = mock_run.call_args
            assert kwargs["arg_type"] == "float[]"

    def test_return_type_float(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--return-type", "float"])
            _, kwargs = mock_run.call_args
            assert kwargs["return_type"] == "float"

    def test_state_flag(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "--state", "gain:double:1.0"])
            args, _ = mock_run.call_args
            assert args[3] is not None

    def test_dest_positional(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj", "/tmp/dest"])
            args, _ = mock_run.call_args
            from pathlib import Path
            assert args[1] == Path("/tmp/dest")

    def test_no_objects_passes_none(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj"])
            args, _ = mock_run.call_args
            assert args[2] is None

    def test_no_state_passes_none(self):
        with patch("just_makeit._new.run") as mock_run:
            _run(["myproj"])
            args, _ = mock_run.call_args
            assert args[3] is None
