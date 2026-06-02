"""Unit tests for just_makeit._script — exercises all helper functions."""

from pathlib import Path

import pytest

from just_makeit import _script
from just_makeit import _config as C


# ── minimal TOML helpers ──────────────────────────────────────────────────────


def _write_toml(root: Path, text: str) -> None:
    (root / C.FILENAME).write_text(text, encoding="utf-8")


_MINIMAL = """\
[project]
name = "proj"
version = "0.1.0"
build = "cmake"
perf = "false"
pytest = "false"
pytest_benchmark = "false"
schema = "6"
"""

_WITH_COMPONENT = (
    _MINIMAL
    + """
[engine]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "false"
no_step = "false"

[[engine.state]]
name = "gain"
type = "double"
default = "1.0"
"""
)

_WITH_MODULE = (
    _MINIMAL
    + """
[module.dsp]
objects = ["nco"]

[nco]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"
"""
)


# ── pure helpers ──────────────────────────────────────────────────────────────


class TestQ:
    def test_no_quote_plain(self):
        assert _script._q("float") == "float"

    def test_quotes_space(self):
        assert _script._q("float _Complex") == '"float _Complex"'

    def test_quotes_bracket(self):
        assert _script._q("float[]") == '"float[]"'

    def test_quotes_parens(self):
        assert _script._q("create(n, p)") == '"create(n, p)"'

    def test_no_quote_underscore(self):
        assert _script._q("uint32_t") == "uint32_t"


class TestFlag:
    def test_plain_value(self):
        result = _script._flag("--arg-type", "float")
        assert result == "    --arg-type float \\\n"

    def test_quoted_value(self):
        result = _script._flag("--arg-type", "float _Complex")
        assert result == '    --arg-type "float _Complex" \\\n'


class TestBoolFlag:
    def test_format(self):
        assert _script._bool_flag("--mutable") == "    --mutable \\\n"

    def test_no_state(self):
        assert _script._bool_flag("--no-state") == "    --no-state \\\n"


class TestRenderCmd:
    def test_no_flags(self):
        result = _script._render_cmd(["just-makeit", "new", "proj"], [])
        assert result == "just-makeit new proj\n"

    def test_with_one_flag(self):
        flags = ["    --arg-type float \\\n"]
        result = _script._render_cmd(["just-makeit", "object", "fir"], flags)
        assert "--arg-type float" in result
        assert result.endswith("\n")
        assert not result.endswith("\\\n")

    def test_trailing_backslash_stripped_from_last_flag(self):
        flags = [
            "    --module dsp \\\n",
            "    --arg-type float \\\n",
        ]
        result = _script._render_cmd(["just-makeit", "object", "fir"], flags)
        lines = result.splitlines()
        assert not lines[-1].endswith("\\")


# ── method flags ─────────────────────────────────────────────────────────────


class TestMethodFlags:
    def test_empty_no_module(self):
        assert _script._method_flags({}, None) == []

    def test_with_module(self):
        flags = _script._method_flags({}, "dsp")
        assert any("--module" in f and "dsp" in f for f in flags)

    def test_arg_type(self):
        flags = _script._method_flags({"arg_type": "float"}, None)
        assert any("--arg-type" in f for f in flags)

    def test_return_type(self):
        flags = _script._method_flags({"return_type": "void"}, None)
        assert any("--return-type" in f for f in flags)

    def test_batch(self):
        flags = _script._method_flags({"batch": True}, None)
        assert any("--batch" in f for f in flags)

    def test_variable_output(self):
        flags = _script._method_flags({"variable_output": True}, None)
        assert any("--variable-output" in f for f in flags)

    def test_multi_output(self):
        flags = _script._method_flags({"multi_output": ["uint8_t"]}, None)
        assert any("--multi-output" in f and "uint8_t" in f for f in flags)

    def test_out_type(self):
        flags = _script._method_flags({"out_type": "float"}, None)
        assert any("--out-type" in f for f in flags)

    def test_out_divisor_gt_1(self):
        flags = _script._method_flags({"out_divisor": 4}, None)
        assert any("--out-divisor" in f and "4" in f for f in flags)

    def test_out_divisor_1_not_emitted(self):
        flags = _script._method_flags({"out_divisor": 1}, None)
        assert not any("--out-divisor" in f for f in flags)

    def test_params(self):
        m = {"params": [{"name": "x", "type": "float"}]}
        flags = _script._method_flags(m, None)
        assert any("--param" in f and "x:float" in f for f in flags)

    def test_no_bench_not_emitted(self):
        flags = _script._method_flags({"no_bench": True}, None)
        assert not any("--no-bench" in f for f in flags)

    def test_varargs(self):
        flags = _script._method_flags({"varargs": True}, None)
        assert any("--varargs" in f for f in flags)

    def test_varargs_false_not_emitted(self):
        flags = _script._method_flags({"varargs": False}, None)
        assert not any("--varargs" in f for f in flags)


# ── property flags ────────────────────────────────────────────────────────────


class TestPropertyFlags:
    def test_type_flag(self):
        flags = _script._property_flags({"type": "double"}, None)
        assert any("--type" in f and "double" in f for f in flags)

    def test_ctype_fallback(self):
        flags = _script._property_flags({"ctype": "size_t"}, None)
        assert any("--type" in f and "size_t" in f for f in flags)

    def test_writable(self):
        flags = _script._property_flags(
            {"type": "double", "writable": True}, None
        )
        assert any("--writable" in f for f in flags)

    def test_no_writable_by_default(self):
        flags = _script._property_flags({"type": "double"}, None)
        assert not any("--writable" in f for f in flags)

    def test_field(self):
        flags = _script._property_flags(
            {"type": "double", "field": True}, None
        )
        assert any("--field" in f for f in flags)

    def test_with_module(self):
        flags = _script._property_flags({"type": "double"}, "dsp")
        assert any("--module" in f and "dsp" in f for f in flags)


# ── function flags ────────────────────────────────────────────────────────────


class TestFunctionFlags:
    def test_always_has_module(self):
        flags = _script._function_flags({}, "dsp")
        assert any("--module" in f and "dsp" in f for f in flags)

    def test_return_type(self):
        flags = _script._function_flags({"return_type": "float"}, "dsp")
        assert any("--return-type" in f for f in flags)

    def test_params(self):
        fn = {"params": [{"name": "x", "type": "float"}]}
        flags = _script._function_flags(fn, "dsp")
        assert any("--param" in f and "x:float" in f for f in flags)

    def test_doc(self):
        flags = _script._function_flags({"doc": "A description."}, "dsp")
        assert any("--doc" in f for f in flags)

    def test_no_return_type_not_emitted(self):
        flags = _script._function_flags({}, "dsp")
        assert not any("--return-type" in f for f in flags)


# ── run() ─────────────────────────────────────────────────────────────────────


class TestRun:
    def test_no_toml_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            _script.run(tmp_path)

    def test_minimal_prints_new(self, tmp_path, capsys):
        _write_toml(tmp_path, _MINIMAL)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "just-makeit new proj" in out

    def test_version_10_emits_config(self, tmp_path, capsys):
        toml = _MINIMAL.replace('version = "0.1.0"', 'version = "1.0.0"')
        _write_toml(tmp_path, toml)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "config version 1.0.0" in out

    def test_version_010_no_config_cmd(self, tmp_path, capsys):
        _write_toml(tmp_path, _MINIMAL)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "config version" not in out

    def test_make_build_system_flag(self, tmp_path, capsys):
        toml = _MINIMAL.replace('build = "cmake"', 'build = "make"')
        _write_toml(tmp_path, toml)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "--build-system make" in out

    def test_pytest_flag(self, tmp_path, capsys):
        toml = _MINIMAL.replace('pytest = "false"', 'pytest = "true"')
        _write_toml(tmp_path, toml)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "--pytest" in out

    def test_standalone_component(self, tmp_path, capsys):
        _write_toml(tmp_path, _WITH_COMPONENT)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "just-makeit object engine" in out

    def test_state_var_in_output(self, tmp_path, capsys):
        _write_toml(tmp_path, _WITH_COMPONENT)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "gain:double:1.0" in out

    def test_module_emitted(self, tmp_path, capsys):
        _write_toml(tmp_path, _WITH_MODULE)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "just-makeit module dsp" in out

    def test_module_object_emitted(self, tmp_path, capsys):
        _write_toml(tmp_path, _WITH_MODULE)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "just-makeit object nco" in out

    def test_module_object_has_module_flag(self, tmp_path, capsys):
        _write_toml(tmp_path, _WITH_MODULE)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "--module dsp" in out

    def test_perf_flag(self, tmp_path, capsys):
        toml = _MINIMAL.replace('perf = "false"', 'perf = "true"')
        _write_toml(tmp_path, toml)
        _script.run(tmp_path)
        out = capsys.readouterr().out
        assert "--perf" in out
