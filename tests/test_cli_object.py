"""Unit tests for just_makeit._cli_object."""

import pytest
from unittest.mock import patch


def _run(args):
    from just_makeit import _cli_object
    _cli_object.run(args)


class TestCliObject:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--module"])

    def test_arg_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--arg-type"])

    def test_return_type_array_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--return-type", "float[]"])

    def test_arg_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--arg-type", "notatype"])

    def test_array_arg_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--array-arg"])

    def test_array_arg_bad_format_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--array-arg", "nocolon"])

    def test_no_state_and_state_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--no-state", "--state", "x:float:0.0"])

    def test_init_param_requires_no_state(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--init-param", "n:int:4"])

    def test_class_name_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["nco", "--class-name"])

    def test_unknown_arg_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--unknown"])

    def test_valid_minimal_call(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir"])
            mock_run.assert_called_once()
            args = mock_run.call_args[0]
            assert args[1] == "fir"

    def test_mutable_flag(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["nco", "--mutable"])
            _, kwargs = mock_run.call_args
            assert kwargs["mutable"] is True

    def test_no_state_flag(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--no-state"])
            _, kwargs = mock_run.call_args
            assert kwargs["no_state"] is True

    def test_no_step_flag(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--no-step"])
            _, kwargs = mock_run.call_args
            assert kwargs["no_step"] is True

    def test_class_name(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["nco", "--class-name", "NCO"])
            _, kwargs = mock_run.call_args
            assert kwargs["class_name"] == "NCO"

    def test_module_and_arg_type(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--module", "signal", "--arg-type", "float"])
            args = mock_run.call_args[0]
            assert args[2] == "signal"
            _, kwargs = mock_run.call_args
            assert kwargs["arg_type"] == "float"

    def test_state_var_parsed(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["gain", "--state", "gain:double:1.0"])
            mock_run.assert_called_once()

    def test_no_state_with_init_param(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--no-state", "--init-param", "n:int:64"])
            _, kwargs = mock_run.call_args
            assert kwargs["no_state"] is True
            assert len(kwargs["init_params"]) == 1

    def test_perf_flag(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--perf"])
            _, kwargs = mock_run.call_args
            assert kwargs["perf"] is True

    def test_return_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--return-type"])

    def test_arg_type_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--arg-type", "notatype[]"])

    def test_arg_type_array_valid(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--arg-type", "float[]"])
            _, kwargs = mock_run.call_args
            assert kwargs["arg_type"] == "float[]"

    def test_array_arg_bad_dtype_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--array-arg", "buf:notatype"])

    def test_array_arg_valid(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--array-arg", "buf:float32"])
            _, kwargs = mock_run.call_args
            assert len(kwargs["array_args"]) == 1

    def test_multi_output_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--multi-output"])

    def test_multi_output_bad_type_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--multi-output", "notatype"])

    def test_multi_output_valid(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--multi-output", "float"])
            _, kwargs = mock_run.call_args
            assert "float" in kwargs["multi_output"]

    def test_method_name_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--method-name"])

    def test_method_name_flag(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--method-name", "process"])
            _, kwargs = mock_run.call_args
            assert kwargs["method_name"] == "process"

    def test_impl_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--impl"])

    def test_replace_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "--replace"])

    def test_impl_loading(self):
        with patch("just_makeit._object.run") as mock_run:
            with patch("just_makeit._impl.load_impl", return_value="body") as mock_load:
                _run(["fir", "--impl", "src.c::fir_step"])
                mock_load.assert_called_once_with("src.c::fir_step", [])
                _, kwargs = mock_run.call_args
                assert kwargs.get("impl_body") == "body"

    def test_variable_output_sets_no_step(self):
        with patch("just_makeit._object.run") as mock_run:
            _run(["fir", "--variable-output"])
            _, kwargs = mock_run.call_args
            assert kwargs["no_step"] is True
            assert kwargs["variable_output"] is True
