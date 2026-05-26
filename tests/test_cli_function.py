"""Unit tests for just_makeit._cli_function."""

import pytest
from unittest.mock import patch, MagicMock


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
            _run(["magnitude_db", "--module", "dsp",
                  "--param", "x:float", "--return-type", "float"])
            mock_run.assert_called_once()
            _, kwargs = mock_run.call_args
            assert kwargs["return_type"] == "float"
            assert ("x", "float") in kwargs["params"]
