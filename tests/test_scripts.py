"""Unit tests for just_makeit._scripts."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from just_makeit import _scripts as S


class TestRunTests:
    def test_exits_with_subprocess_returncode(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with pytest.raises(SystemExit) as exc:
                S.run_tests()
            assert exc.value.code == 0

    def test_exits_nonzero_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with pytest.raises(SystemExit) as exc:
                S.run_tests()
            assert exc.value.code == 1

    def test_passes_argv_to_subprocess(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch.object(
                sys, "argv", ["jm-run-tests", "-v", "-k", "foo"]
            ):
                with pytest.raises(SystemExit):
                    S.run_tests()
            cmd = mock_run.call_args[0][0]
            assert "-v" in cmd and "-k" in cmd and "foo" in cmd

    def test_an_empty_collection_fails(self):
        """gh-2642: pytest's exit 5 (no tests collected) is not a pass."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=5)
            with pytest.raises(SystemExit) as exc:
                S.run_tests()
            assert exc.value.code == 5
