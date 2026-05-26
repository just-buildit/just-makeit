"""Unit tests for just_makeit._cli_remove."""

import pytest
from unittest.mock import patch


def _run(args):
    from just_makeit import _cli_remove
    _cli_remove.run(args)


class TestCliRemove:
    def test_no_args_exits(self):
        with pytest.raises(SystemExit):
            _run([])

    def test_bad_kind_exits(self):
        with pytest.raises(SystemExit):
            _run(["badkind", "foo"])

    def test_module_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["object", "foo", "--module"])

    def test_object_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _run(["method", "foo", "--object"])

    def test_unknown_option_exits(self):
        with pytest.raises(SystemExit):
            _run(["object", "foo", "--unknown"])

    def test_remove_object(self):
        with patch("just_makeit._remove.run") as mock_run:
            _run(["object", "fir"])
            mock_run.assert_called_once()
            args = mock_run.call_args[0]
            assert args[1] == "object"
            assert args[2] == "fir"

    def test_force_flag(self):
        with patch("just_makeit._remove.run") as mock_run:
            _run(["object", "fir", "--force"])
            kwargs = mock_run.call_args[1]
            assert kwargs["force"] is True

    def test_remove_function_with_module(self):
        with patch("just_makeit._remove.run") as mock_run:
            _run(["function", "magnitude_db", "--module", "dsp"])
            mock_run.assert_called_once()
            kwargs = mock_run.call_args[1]
            assert kwargs["module"] == "dsp"
