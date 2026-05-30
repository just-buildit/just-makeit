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

    def test_multi_output_valid(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--multi-output", "float"])
            args, _ = mock_run.call_args
            assert "float" in args[7]  # multi_output is positional arg 7

    def test_param_valid_scalar(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--param", "n:int"])
            _, kwargs = mock_run.call_args
            assert ("n", "int") in kwargs.get("params", [])

    def test_param_valid_array(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--param", "coeffs:float[]"])
            _, kwargs = mock_run.call_args
            assert ("coeffs", "float[]") in kwargs.get("params", [])

    def test_param_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param", "x:notatype[]"])

    def test_param_bad_scalar_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param", "x:notatype"])

    def test_out_divisor_valid(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--out-divisor", "4"])
            _, kwargs = mock_run.call_args
            assert kwargs.get("out_divisor") == 4

    def test_out_divisor_negative_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--out-divisor", "-1"])

    def test_max_out_valid(self):
        """Phase 2 row 2: --max-out N forwards to _method.run(max_out=N)."""
        with patch("just_makeit._method.run") as mock_run:
            _run(
                [
                    "fir",
                    "execute",
                    "--variable-output",
                    "--max-out",
                    "1024",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs.get("max_out") == 1024

    def test_max_out_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--max-out"])

    def test_max_out_zero_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--max-out", "0"])

    def test_max_out_non_integer_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--max-out", "lots"])

    def test_out_divisor_non_int_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--out-divisor", "abc"])

    def test_result_field_valid(self):
        """Phase 2 row 4: --result-field name:T (repeatable) forwards to
        _method.run(result_fields=[{name, type}, ...])."""
        with patch("just_makeit._method.run") as mock_run:
            _run(
                [
                    "det",
                    "detect",
                    "--result-field",
                    "sample_index:size_t",
                    "--result-field",
                    "magnitude:float",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs["result_fields"] == [
                {"name": "sample_index", "type": "size_t"},
                {"name": "magnitude", "type": "float"},
            ]

    def test_result_field_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["det", "detect", "--result-field"])

    def test_result_field_bad_format_exits(self):
        with pytest.raises(SystemExit):
            _run(["det", "detect", "--result-field", "nocolon"])

    def test_result_field_bad_type_exits(self):
        with pytest.raises(SystemExit):
            _run(["det", "detect", "--result-field", "x:notatype"])

    def test_out_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--out-type"])

    def test_out_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--out-type", "notatype"])

    def test_out_type_valid(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--out-type", "float"])
            _, kwargs = mock_run.call_args
            assert kwargs.get("out_type") == "float"

    def test_arg_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--arg-type"])

    def test_arg_type_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--arg-type", "notatype[]"])

    def test_arg_type_bad_scalar_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--arg-type", "notatype"])

    def test_arg_type_array_valid(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--arg-type", "float[]"])
            args = mock_run.call_args[0]
            assert args[4] == "float[]"

    def test_return_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--return-type"])

    def test_return_type_valid(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--return-type", "float"])
            args = mock_run.call_args[0]
            assert args[5] == "float"

    def test_impl_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--impl"])

    def test_replace_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--replace"])

    def test_impl_loading(self):
        with patch("just_makeit._method.run") as mock_run:
            with patch("just_makeit._impl.load_impl", return_value="body") as mock_load:
                _run(["fir", "execute", "--impl", "src.c::fir_step"])
                mock_load.assert_called_once_with("src.c::fir_step", [])
                _, kwargs = mock_run.call_args
                assert kwargs.get("impl_body") == "body"

    def test_replace_with_impl(self):
        with patch("just_makeit._method.run"):
            with patch("just_makeit._impl.load_impl", return_value="body"):
                with patch("just_makeit._impl.parse_replace", return_value=("a", "b")):
                    _run(
                        [
                            "fir",
                            "execute",
                            "--replace",
                            "a::b",
                            "--impl",
                            "src.c::fir_step",
                        ]
                    )
