"""Unit tests for just_makeit._cli dispatch layer."""

import ast
import inspect
import re
import sys
import pytest
from unittest.mock import patch


def _main(args):
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


def _dispatched_commands() -> set[str]:
    """Every command name the dispatcher in `main()` actually handles.

    Derived from the source, not a hand-maintained list: we AST-parse
    `main()` and collect every string literal compared against the local
    `cmd` variable, whether via ``cmd == "x"`` or ``cmd in ("x", "y")``.
    This is the ground truth of "what commands exist" — if a branch is
    added, this set grows automatically.
    """
    from just_makeit import _cli

    tree = ast.parse(inspect.getsource(_cli.main))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "cmd"):
            continue
        for comp in node.comparators:
            elts = (
                comp.elts
                if isinstance(comp, (ast.Tuple, ast.List))
                else [comp]
            )
            for e in elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    found.add(e.value)
    return found


def _documented_commands() -> set[str]:
    """Command names listed under ``Commands:`` in the ``jm help`` text.

    A command entry is a line indented exactly two spaces whose first token
    is a lowercase word (four-space indents are option lines; other headings
    like ``Types (…)`` / ``Examples:`` start the next section).
    """
    from just_makeit import _cli

    documented: set[str] = set()
    in_commands = False
    for line in _cli._USAGE.splitlines():
        if line.startswith("Commands:"):
            in_commands = True
            continue
        if in_commands and re.match(r"^[A-Za-z]", line):
            break  # left the Commands block (Types / Examples heading)
        if in_commands:
            m = re.match(r"^  ([a-z][\w-]*)", line)
            if m:
                documented.add(m.group(1))
    return documented


# `help` and the `--version`/`-V`/`-h`/`--help` aliases are resolved at the top
# of main() before the dispatch chain, so they are documented but never appear
# as a `cmd == ...` branch. `help` is the only one listed by its bare name.
_EARLY_HANDLED = {"help"}


class TestHelpMatchesDispatch:
    """`jm help` must list exactly the commands the dispatcher handles.

    This is the anti-drift gate: `bind` and `upgrade` each shipped for months
    dispatchable but absent from `jm help` because the two lived in different
    places and nothing tied them together. Now they cannot diverge silently —
    add a command branch without documenting it (or vice versa) and this fails.
    """

    def test_every_dispatched_command_is_documented(self):
        missing = _dispatched_commands() - _documented_commands()
        assert not missing, (
            "these commands are handled by main() but missing from the "
            f"Commands: section of jm help (_USAGE): {sorted(missing)}. "
            "Add an entry so `jm help` lists them."
        )

    def test_every_documented_command_is_dispatched(self):
        extra = (
            _documented_commands() - _dispatched_commands() - _EARLY_HANDLED
        )
        assert not extra, (
            "these commands are listed in jm help but main() has no branch "
            f"for them: {sorted(extra)}. Either wire them up or remove the "
            "help entry (early-handled aliases belong in _EARLY_HANDLED)."
        )
