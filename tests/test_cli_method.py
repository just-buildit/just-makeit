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
            assert (
                kwargs.get("variable_output") is True
                or mock_run.call_args[0][5]
            )

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
            assert ("n", "int", "") in kwargs.get("params", [])

    def test_param_valid_array(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--param", "coeffs:float[]"])
            _, kwargs = mock_run.call_args
            assert ("coeffs", "float[]", "") in kwargs.get("params", [])

    def test_scalar_default_parsed(self):
        # gh-240: `name:type=default` → 3-tuple carrying the default.
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--param", "gain:double=1.5"])
            _, kwargs = mock_run.call_args
            assert ("gain", "double", "1.5") in kwargs.get("params", [])

    def test_default_on_array_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param", "buf:float[]=x"])

    def test_required_after_default_exits(self):
        with pytest.raises(SystemExit):
            _run(
                [
                    "fir",
                    "execute",
                    "--param",
                    "gain:double=1.0",
                    "--param",
                    "n:size_t",
                ]
            )

    def test_param_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param", "x:notatype[]"])

    def test_param_bad_scalar_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--param", "x:notatype"])

    def test_extra_arg_accepted_as_alias_for_param(self):
        # gh-123: --extra-arg is a synonym for --param
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute", "--extra-arg", "dump_now:bool"])
            _, kwargs = mock_run.call_args
            assert ("dump_now", "bool", "") in kwargs.get("params", [])

    def test_extra_arg_bad_format_exits(self):
        with pytest.raises(SystemExit):
            _run(["fir", "execute", "--extra-arg", "nocolon"])

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

    def test_single_with_result_field_and_struct_return(self):
        # gh-244: --single + a struct --return-type forwards single=True and
        # the struct return type is accepted (exempt from the scalar allowlist).
        with patch("just_makeit._method.run") as mock_run:
            _run(
                [
                    "tm",
                    "analyze",
                    "--result-field",
                    "snr:float",
                    "--return-type",
                    "tone_metrics_t",
                    "--single",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs["single"] is True
            args = mock_run.call_args[0]
            assert args[5] == "tone_metrics_t"  # return_type (positional 5)

    def test_single_without_result_field_exits(self):
        with pytest.raises(SystemExit):
            _run(["tm", "analyze", "--single"])

    def test_record_name_flag_forwarded(self):
        # gh-257: --record-name forwards a chosen public structseq name.
        with patch("just_makeit._method.run") as mock_run:
            _run(
                [
                    "tm",
                    "analyze",
                    "--result-field",
                    "snr:float",
                    "--return-type",
                    "tone_meas_t",
                    "--single",
                    "--record-name",
                    "ToneMetrics",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs["record_name"] == "ToneMetrics"

    def test_record_name_requires_value(self):
        with pytest.raises(SystemExit):
            _run(["tm", "analyze", "--record-name"])

    def test_record_module_flag_forwarded(self):
        # gh-261: --record-module forwards the structseq __module__ qualifier.
        with patch("just_makeit._method.run") as mock_run:
            _run(
                [
                    "tm",
                    "analyze",
                    "--result-field",
                    "snr:float",
                    "--return-type",
                    "tone_meas_t",
                    "--single",
                    "--record-module",
                    "my_pkg.dsp",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs["record_module"] == "my_pkg.dsp"

    def test_record_module_requires_value(self):
        with pytest.raises(SystemExit):
            _run(["tm", "analyze", "--record-module"])

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
            with patch(
                "just_makeit._impl.load_impl", return_value="body"
            ) as mock_load:
                _run(["fir", "execute", "--impl", "src.c::fir_step"])
                mock_load.assert_called_once_with("src.c::fir_step", [])
                _, kwargs = mock_run.call_args
                assert kwargs.get("impl_body") == "body"

    def test_replace_with_impl(self):
        with patch("just_makeit._method.run"):
            with patch("just_makeit._impl.load_impl", return_value="body"):
                with patch(
                    "just_makeit._impl.parse_replace", return_value=("a", "b")
                ):
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

    def test_varargs_flag_forwarded(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "configure", "--varargs"])
            _, kwargs = mock_run.call_args
            assert kwargs.get("varargs") is True

    def test_varargs_default_false(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute"])
            _, kwargs = mock_run.call_args
            assert not kwargs.get("varargs")


class TestGh805Flags:
    """gh-805 §A2 + §B reach `_method.run` from the CLI, and each
    value-taking flag refuses a missing value rather than swallowing the
    next token as its argument."""

    def test_fn_forwards(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["dp_tlm", "emit", "--fn", "dp_tlm_emit_checked"])
            assert mock_run.call_args[1]["fn"] == "dp_tlm_emit_checked"

    def test_error_negative_forwards(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(["dp_tlm", "probe_id", "--error-negative"])
            assert mock_run.call_args[1]["error_negative"] is True

    def test_error_and_message_forward(self):
        with patch("just_makeit._method.run") as mock_run:
            _run(
                [
                    "dp_tlm",
                    "probe_id",
                    "--error-negative",
                    "--error",
                    "KeyError",
                    "--error-message",
                    "no probe by that name",
                ]
            )
            kw = mock_run.call_args[1]
            assert kw["error"] == "KeyError"
            assert kw["error_message"] == "no probe by that name"

    def test_defaults_are_absent_when_not_given(self):
        """Zero churn: a method declared without these keys must not gain
        them, or every existing manifest rewrites on the next apply."""
        with patch("just_makeit._method.run") as mock_run:
            _run(["fir", "execute"])
            kw = mock_run.call_args[1]
            assert kw["fn"] == ""
            assert kw["error_negative"] is False
            assert kw["error"] == ""
            assert kw["error_message"] == ""

    @pytest.mark.parametrize("flag", ["--fn", "--error", "--error-message"])
    def test_a_value_taking_flag_refuses_a_missing_value(self, flag):
        with pytest.raises(SystemExit):
            _run(["dp_tlm", "emit", flag])
