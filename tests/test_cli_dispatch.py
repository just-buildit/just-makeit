"""Unit tests for just_makeit._cli dispatch layer."""

import sys
import pytest
from unittest.mock import patch, MagicMock


def _main(args):
    import sys
    from just_makeit._cli import main
    with patch.object(sys, "argv", ["jm"] + args):
        main()


class TestDispatch:
    def test_no_args_prints_usage(self, capsys):
        _main([])
        out = capsys.readouterr().out
        assert "Usage" in out or "just-makeit" in out

    def test_help_prints_usage(self, capsys):
        _main(["help"])
        out = capsys.readouterr().out
        assert "Commands" in out or "just-makeit" in out

    def test_version_prints_version(self, capsys):
        _main(["version"])
        out = capsys.readouterr().out
        assert out.strip()  # something was printed

    def test_unknown_command_exits(self, capsys):
        with pytest.raises(SystemExit):
            _main(["notacommand"])

    def test_dry_run_dispatches(self):
        with patch("just_makeit._build.cmd_dry_run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["dry-run"])
            mock.assert_called_once()

    def test_build_dispatches(self):
        with patch("just_makeit._build.cmd_build") as mock:
            _main(["build"])
            mock.assert_called_once_with([])

    def test_test_dispatches(self):
        with patch("just_makeit._build.cmd_test") as mock:
            _main(["test"])
            mock.assert_called_once_with([])

    def test_example_no_name(self):
        with patch("just_makeit._example.run") as mock:
            _main(["example"])
            mock.assert_called_once_with(None)

    def test_example_with_name(self):
        with patch("just_makeit._example.run") as mock:
            _main(["example", "fir_filter"])
            mock.assert_called_once_with("fir_filter")

    def test_apply_dispatches(self):
        with patch("just_makeit._apply.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["apply"])
            mock.assert_called_once()

    def test_script_dispatches(self):
        with patch("just_makeit._script.run") as mock:
            with patch("just_makeit._cli._warn_schema"):
                _main(["script"])
            mock.assert_called_once()

    def test_upgrade_dispatches(self):
        with patch("just_makeit._upgrade.run") as mock:
            _main(["upgrade"])
            mock.assert_called_once()
