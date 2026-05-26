"""Unit tests for just_makeit._cli_method."""

import pytest
from unittest.mock import patch


def _run(args):
    from just_makeit import _cli_method
    _cli_method.run(args)


class TestCliMethod:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_one_arg_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir"])

    def test_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--module"])

    def test_param_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param"])

    def test_param_bad_format_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param", "nocolon"])

    def test_multi_output_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--multi-output"])

    def test_multi_output_bad_type_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--multi-output", "notatype"])

    def test_out_divisor_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--out-divisor"])

    def test_out_divisor_zero_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--out-divisor", "0"])

    def test_return_type_array_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--return-type", "float[]"])

    def test_unknown_arg_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--unknown"])

    def test_variable_output_flag(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--variable-output"])
            _, kwargs = mock_run.call_args
            assert kwargs.get("variable_output") is True or mock_run.call_args[0][5]

    def test_no_bench_flag(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--no-bench"])
            _, kwargs = mock_run.call_args
            assert kwargs.get("no_bench") is True

    def test_batch_flag(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--batch"])
            _, kwargs = mock_run.call_args
            assert kwargs.get("batch") is True

    def test_valid_minimal_call(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute"])
            mock_run.assert_called_once()
            args = mock_run.call_args[0]
            assert args[1] == "fir"
            assert args[2] == "execute"

    def test_module_and_arg_type(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["nco", "step", "--module", "signal", "--arg-type", "float"])
            args = mock_run.call_args[0]
            assert args[3] == "signal"
            assert args[4] == "float"
