"""Unit tests for just_makeit._cli_parse."""

import pytest


def _parse_state(tokens, i=0):
    from just_makeit._cli_parse import parse_state_flag

    return parse_state_flag(tokens, i)


def _parse_init(tokens, i=0):
    from just_makeit._cli_parse import parse_init_param_flag

    return parse_init_param_flag(tokens, i)


class TestParseStateFlag:
    def test_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _parse_state(["--state"])

    def test_bad_format_no_colon_exits(self):
        with pytest.raises(SystemExit):
            _parse_state(["--state", "justname"])

    def test_unsupported_type_exits(self):
        with pytest.raises(SystemExit):
            _parse_state(["--state", "x:notareal_t"])

    def test_scalar_with_default(self):
        (name, ctype, default), new_i = _parse_state(
            ["--state", "gain:double:1.0"]
        )
        assert name == "gain"
        assert ctype == "double"
        assert default == "1.0"
        assert new_i == 2

    def test_scalar_default_from_meta(self):
        (name, ctype, default), new_i = _parse_state(["--state", "x:float"])
        assert ctype == "float"
        assert default == "0.0f"

    def test_array_type_ignores_default(self, capsys):
        (name, ctype, default), new_i = _parse_state(
            ["--state", "buf:float[64]:ignored"]
        )
        assert name == "buf"
        assert ctype == "float[64]"
        assert default == ""
        assert "warning" in capsys.readouterr().err.lower()

    def test_array_type_no_default(self):
        (name, ctype, default), new_i = _parse_state(
            ["--state", "buf:double[16]"]
        )
        assert ctype == "double[16]"
        assert default == ""
        assert new_i == 2

    def test_non_zero_start_index(self):
        tokens = ["--object", "fir", "--state", "n:int:4"]
        (name, ctype, default), new_i = _parse_state(tokens, i=2)
        assert name == "n"
        assert new_i == 4


class TestParseInitParamFlag:
    def test_missing_value_exits(self):
        with pytest.raises(SystemExit):
            _parse_init(["--init-param"])

    def test_bad_format_no_colon_exits(self):
        with pytest.raises(SystemExit):
            _parse_init(["--init-param", "justname"])

    def test_unsupported_scalar_type_exits(self):
        with pytest.raises(SystemExit):
            _parse_init(["--init-param", "n:notareal_t"])

    def test_optional_on_non_array_exits(self):
        with pytest.raises(SystemExit):
            _parse_init(["--init-param", "n:int:optional"])

    def test_scalar_with_default(self):
        result, new_i = _parse_init(["--init-param", "n:int:64"])
        name, ctype, default = result[0], result[1], result[2]
        assert name == "n"
        assert ctype == "int"
        assert default == "64"
        assert new_i == 2

    def test_scalar_default_from_meta(self):
        result, new_i = _parse_init(["--init-param", "gain:float"])
        assert result[2] == "0.0f"

    def test_array_required(self):
        result, new_i = _parse_init(["--init-param", "coeffs:float[]"])
        name, ctype, default = result[0], result[1], result[2]
        assert name == "coeffs"
        assert ctype == "float[]"
        assert default == ""
        assert result[6] is False  # not optional

    def test_optional_array(self):
        result, new_i = _parse_init(["--init-param", "w:float[]:optional"])
        assert result[6] is True  # optional=True
        assert result[7] == ""  # no create_fn

    def test_optional_array_with_create_fn(self):
        result, new_i = _parse_init(
            ["--init-param", "w:float[]:optional:hann_window"]
        )
        assert result[6] is True
        assert result[7] == "hann_window"
