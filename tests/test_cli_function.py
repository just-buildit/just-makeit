"""Unit tests for just_makeit._cli_function."""

import pytest
from unittest.mock import patch


def _run(args):
    from just_makeit import _cli_function

    _cli_function.run(args)


class TestCliFunction:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_no_module_exits(self):
        with patch("just_makeit._function.run"):
            with pytest.raises(SystemExit):
                _run(["magnitude_db"])

    def test_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["magnitude_db", "--module"])

    def test_param_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--param"])

    def test_param_bad_format_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--param", "nocolon"])

    def test_return_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--return-type"])

    def test_unknown_arg_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--unknown"])

    def test_inline_flag(self):
        with patch("just_makeit._function.run") as mock_run:
            _run(["fn", "--module", "dsp", "--inline"])
            _, kwargs = mock_run.call_args
            assert kwargs["inline"] is True

    def test_valid_call(self):
        with patch("just_makeit._function.run") as mock_run:
            _run(
                [
                    "magnitude_db",
                    "--module",
                    "dsp",
                    "--param",
                    "x:float",
                    "--return-type",
                    "float",
                ]
            )
            mock_run.assert_called_once()
            _, kwargs = mock_run.call_args
            assert kwargs["return_type"] == "float"
            assert ("x", "float", False) in kwargs["params"]

    def test_doc_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--doc"])

    def test_doc_flag(self):
        with patch("just_makeit._function.run") as mock_run:
            _run(["fn", "--module", "dsp", "--doc", "Compute magnitude."])
            args, _ = mock_run.call_args
            assert args[3] == "Compute magnitude."  # doc is positional arg 3

    def test_param_bad_array_elem_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--param", "x:notatype[]"])

    def test_param_bad_scalar_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--param", "x:notatype"])

    def test_param_valid_array(self):
        with patch("just_makeit._function.run") as mock_run:
            _run(["fn", "--module", "dsp", "--param", "buf:float[]"])
            _, kwargs = mock_run.call_args
            assert ("buf", "float[]", False) in kwargs["params"]

    def test_out_param_array(self):
        with patch("just_makeit._function.run") as mock_run:
            _run(["fn", "--module", "dsp", "--out-param", "out:float[]"])
            _, kwargs = mock_run.call_args
            assert ("out", "float[]", True) in kwargs["params"]

    def test_out_param_scalar_exits(self):
        # --out-param only applies to array params; scalars are rejected.
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--out-param", "x:float"])

    def test_return_type_bad_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--return-type", "notatype"])

    def test_impl_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--impl"])

    def test_replace_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--replace"])

    def test_impl_loading(self):
        with patch("just_makeit._function.run") as mock_run:
            with patch("just_makeit._impl.load_impl", return_value="body") as mock_load:
                _run(["fn", "--module", "dsp", "--impl", "src.c::fn"])
                mock_load.assert_called_once_with("src.c::fn", [])
                _, kwargs = mock_run.call_args
                assert kwargs.get("impl_body") == "body"

    def test_result_field_valid(self):
        """Phase 2 row 4: --result-field name:T forwards to
        _function.run(result_fields=[...]); brings function to parity
        with method's TOML-only result_fields key."""
        with patch("just_makeit._function.run") as mock_run:
            _run(
                [
                    "find_peaks",
                    "--module",
                    "dsp",
                    "--result-field",
                    "idx:size_t",
                    "--result-field",
                    "magnitude:float",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs["result_fields"] == [
                {"name": "idx", "type": "size_t"},
                {"name": "magnitude", "type": "float"},
            ]

    def test_result_field_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--result-field"])

    def test_result_field_bad_format_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--result-field", "nocolon"])

    def test_result_field_bad_type_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--result-field", "x:notatype"])

    def test_replace_with_impl(self):
        with patch("just_makeit._function.run"):
            with patch("just_makeit._impl.load_impl", return_value="body"):
                with patch("just_makeit._impl.parse_replace", return_value=("a", "b")):
                    _run(
                        [
                            "fn",
                            "--module",
                            "dsp",
                            "--replace",
                            "a::b",
                            "--impl",
                            "src.c::fn",
                        ]
                    )

    def test_out_type_valid(self):
        """Phase 2 row 3: --out-type T forwards to _function.run(out_type=T).
        Brings jm function to parity with jm method, which already supports it."""
        with patch("just_makeit._function.run") as mock_run:
            _run(
                [
                    "magnitude_db",
                    "--module",
                    "dsp",
                    "--param",
                    "x:float[]",
                    "--out-type",
                    "float",
                ]
            )
            _, kwargs = mock_run.call_args
            assert kwargs.get("out_type") == "float"

    def test_out_type_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--out-type"])

    def test_out_type_bad_type_exits(self):
        # const char * has no canonical numpy dtype — rejected.
        with pytest.raises(SystemExit):
            _run(["fn", "--module", "dsp", "--out-type", "const char *"])
