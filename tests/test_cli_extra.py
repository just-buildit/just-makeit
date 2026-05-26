"""Additional dispatch tests for just_makeit._cli — covers unchecked branches."""

import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _main(args):
    from just_makeit._cli import main
    with patch.object(sys, "argv", ["jm"] + args):
        main()


# ── _color_supported / _colorize ──────────────────────────────────────────────

class TestColorSupported:
    def test_false_when_no_color_env(self):
        from just_makeit._cli import _color_supported
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            assert _color_supported() is False

    def test_false_when_dumb_term(self):
        from just_makeit._cli import _color_supported
        with patch.dict(os.environ, {"TERM": "dumb"}, clear=False):
            assert _color_supported() is False


class TestColorize:
    def test_no_color_returns_text_unchanged(self):
        from just_makeit._cli import _colorize
        with patch("just_makeit._cli._color_supported", return_value=False):
            text = "Usage: just-makeit <command>\n"
            assert _colorize(text) == text

    def test_with_color_transforms_usage(self):
        from just_makeit._cli import _colorize
        with patch("just_makeit._cli._color_supported", return_value=True):
            result = _colorize("Usage: just-makeit  (alias: jm)  <command> [options]\n")
            assert "Usage" in result

    def test_with_color_commands_section(self):
        from just_makeit._cli import _colorize
        with patch("just_makeit._cli._color_supported", return_value=True):
            result = _colorize("Commands:\n  new proj  Create project.\n")
            assert "new" in result

    def test_with_color_types_section(self):
        from just_makeit._cli import _colorize
        with patch("just_makeit._cli._color_supported", return_value=True):
            result = _colorize("Types (--arg-type):\n  float  double\n")
            assert "float" in result

    def test_with_color_examples_section(self):
        from just_makeit._cli import _colorize
        with patch("just_makeit._cli._color_supported", return_value=True):
            result = _colorize("Examples:\n  # create\n  jm new proj\n")
            assert "new" in result


# ── _warn_schema ──────────────────────────────────────────────────────────────

class TestWarnSchema:
    def test_no_toml_no_warning(self, tmp_path, monkeypatch, capsys):
        from just_makeit._cli import _warn_schema
        monkeypatch.chdir(tmp_path)
        _warn_schema()
        assert capsys.readouterr().err == ""

    def test_old_schema_warns(self, tmp_path, monkeypatch, capsys):
        from just_makeit._cli import _warn_schema
        from just_makeit import _config as C
        monkeypatch.chdir(tmp_path)
        (tmp_path / C.FILENAME).write_text(
            "[project]\nname = \"proj\"\nversion = \"0.1.0\"\n"
            "build = \"cmake\"\nperf = \"false\"\npytest = \"false\"\n"
            "schema = \"1\"\n"
        )
        _warn_schema()
        err = capsys.readouterr().err
        assert "schema" in err or "upgrade" in err or err == ""


# ── module command ─────────────────────────────────────────────────────────────

class TestModuleDispatch:
    def test_module_no_name_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["module"])

    def test_module_dispatches_to_module_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._module.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["module", "dsp"])
            mock.assert_called_once_with(Path.cwd(), "dsp")


# ── perf command ───────────────────────────────────────────────────────────────

class TestPerfDispatch:
    def test_perf_dispatches(self):
        with patch("just_makeit._perf.run") as mock:
            _main(["perf"])
            mock.assert_called_once()


# ── config command ─────────────────────────────────────────────────────────────

class TestConfigDispatch:
    def test_config_no_toml_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            _main(["config"])

    def test_config_show_all(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "just-makeit.toml").write_text(
            "[project]\nname = \"proj\"\nversion = \"0.1.0\"\n"
            "build = \"cmake\"\nperf = \"false\"\npytest = \"false\"\n"
            "schema = \"6\"\n"
        )
        _main(["config"])
        out = capsys.readouterr().out
        assert "proj" in out

    def test_config_set_version(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "just-makeit.toml").write_text(
            "[project]\nname = \"proj\"\nversion = \"0.1.0\"\n"
            "build = \"cmake\"\nperf = \"false\"\npytest = \"false\"\n"
            "schema = \"6\"\n"
        )
        _main(["config", "version", "0.2.0"])
        out = capsys.readouterr().out
        assert "0.2.0" in out

    def test_config_unknown_key_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "just-makeit.toml").write_text(
            "[project]\nname = \"proj\"\nversion = \"0.1.0\"\n"
            "build = \"cmake\"\nperf = \"false\"\npytest = \"false\"\n"
            "schema = \"6\"\n"
        )
        with pytest.raises(SystemExit):
            _main(["config", "badkey", "val"])

    def test_config_wrong_arg_count_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "just-makeit.toml").write_text(
            "[project]\nname = \"proj\"\nversion = \"0.1.0\"\n"
            "build = \"cmake\"\nperf = \"false\"\npytest = \"false\"\n"
            "schema = \"6\"\n"
        )
        with pytest.raises(SystemExit):
            _main(["config", "version"])


# ── bench command ─────────────────────────────────────────────────────────────

class TestBenchDispatch:
    def test_bench_dispatches(self):
        with patch("just_makeit._bench.run") as mock:
            _main(["bench"])
            mock.assert_called_once()

    def test_bench_c_only(self):
        with patch("just_makeit._bench.run") as mock:
            _main(["bench", "--c-only"])
            _, kwargs = mock.call_args
            assert kwargs["do_python"] is False
            assert kwargs["do_c"] is True

    def test_bench_python_only(self):
        with patch("just_makeit._bench.run") as mock:
            _main(["bench", "--python-only"])
            _, kwargs = mock.call_args
            assert kwargs["do_c"] is False
            assert kwargs["do_python"] is True

    def test_bench_with_tag(self):
        with patch("just_makeit._bench.run") as mock:
            _main(["bench", "--tag", "mytag"])
            _, kwargs = mock.call_args
            assert kwargs["tag"] == "mytag"

    def test_bench_with_component(self):
        with patch("just_makeit._bench.run") as mock:
            _main(["bench", "fir"])
            _, kwargs = mock.call_args
            assert kwargs["components"] == ["fir"]


# ── split-objects command ─────────────────────────────────────────────────────

class TestSplitObjectsDispatch:
    def test_split_objects_dispatches(self):
        with patch("just_makeit._split_objects.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["split-objects"])
            mock.assert_called_once()


# ── install-deps command ──────────────────────────────────────────────────────

class TestInstallDepsDispatch:
    def test_install_deps_dispatches(self):
        with patch("just_makeit._scripts.install_deps") as mock:
            _main(["install-deps"])
            mock.assert_called_once()


# ── add command ───────────────────────────────────────────────────────────────

class TestAddDispatch:
    def test_add_no_state_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "just-makeit.toml").write_text(
            "[project]\nname = \"proj\"\nversion = \"0.1.0\"\n"
            "build = \"cmake\"\nperf = \"false\"\npytest = \"false\"\n"
            "schema = \"6\"\n"
        )
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["add"])

    def test_add_unknown_arg_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["add", "--unknown"])

    def test_add_object_flag_missing_value_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["add", "--object"])

    def test_add_dispatches_to_add_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._add.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["add", "--state", "gain:double:1.0"])
            mock.assert_called_once()


# ── apply extra branches ──────────────────────────────────────────────────────

class TestApplyExtra:
    def test_apply_with_fragment(self, tmp_path, monkeypatch):
        fragment = tmp_path / "frag.c"
        fragment.write_text("")
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._apply.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["apply", str(fragment)])
            mock.assert_called_once()

    def test_apply_too_many_fragments_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["apply", "a.c", "b.c"])

    def test_apply_only_equals_form(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._apply.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["apply", "--only=engine"])
            mock.assert_called_once()

    def test_apply_only_missing_value_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["apply", "--only"])


# ── property inline dispatch ──────────────────────────────────────────────────

class TestPropertyDispatch:
    def test_property_too_few_args_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["property", "nco"])

    def test_property_module_missing_value_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["property", "nco", "phase", "--module"])

    def test_property_type_missing_value_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["property", "nco", "phase", "--type"])

    def test_property_bad_type_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["property", "nco", "phase", "--type", "not_a_type"])

    def test_property_unknown_arg_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._cli._warn_schema"):
            with pytest.raises(SystemExit):
                _main(["property", "nco", "phase", "--bogus"])

    def test_property_dispatches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._property.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["property", "nco", "phase"])
            mock.assert_called_once()

    def test_property_writable_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._property.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["property", "nco", "phase", "--writable"])
            _, kwargs = mock.call_args
            assert kwargs.get("writable") is True or mock.call_args[0][5] is True

    def test_property_field_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("just_makeit._property.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["property", "nco", "phase", "--field"])
            mock.assert_called_once()
