"""The one place jm runs a test suite and reads its verdict (gh-2642).

pytest exits 5 when it collects nothing. That exit was once read as a pass in
two places (`jm test` and the examples gate, `run_generated_pytest`), for
gh-1950's functions-only project, which had no Python test. That shape is
gone: gh-2127 gives a functions-only module a contract test and gh-2640 gives
every project ``src/<pkg>/tests/test_project.py``. An empty collection now
means the tests are gone, and passing it proves nothing.

So every runner states, at its call, whether an empty collection passes
(`empty_passes`), and this module is the only one that turns an exit code
into a verdict. Only ``jm bench`` says yes: a project may ship C benchmarks
and no Python ones. ``tests/test_gh2642_empty_run_fails.py`` refuses a
pytest or unittest command line spelled anywhere else in jm.

unittest is read the same way. Before Python 3.12 ``unittest discover`` exits
0 on an empty run; :func:`unittest` runs it through `UNITTEST_SNIPPET`, which
exits `NO_TESTS_COLLECTED` when nothing ran, on every Python jm supports.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

#: pytest's exit status for "no tests collected"; `UNITTEST_SNIPPET` exits
#: with it too, so one reading covers both runners.
NO_TESTS_COLLECTED = 5

#: ``unittest discover -s <argv[1]> -v`` that exits `NO_TESTS_COLLECTED` when
#: no test ran, 0 when every test passed, and 1 otherwise -- Python 3.12's
#: reading, on every Python.
UNITTEST_SNIPPET = """\
import sys, unittest
prog = unittest.main(
    module=None,
    exit=False,
    argv=["unittest", "discover", "-s", sys.argv[1], "-v"],
)
result = prog.result
sys.exit(5 if not result.testsRun else 0 if result.wasSuccessful() else 1)
"""


@dataclass(frozen=True)
class Outcome:
    """A finished test run and its verdict.

    Attributes
    ----------
    proc : subprocess.CompletedProcess
        The run itself: its ``returncode`` and, when captured, its output.
    empty_passes : bool
        The caller's answer to "is an empty collection a pass here".

    Examples
    --------
    >>> import subprocess
    >>> empty = subprocess.CompletedProcess([], NO_TESTS_COLLECTED)
    >>> Outcome(empty, empty_passes=False).passed
    False
    >>> Outcome(empty, empty_passes=True).passed
    True
    >>> Outcome(subprocess.CompletedProcess([], 0), False).passed
    True
    """

    proc: "subprocess.CompletedProcess[Any]"
    empty_passes: bool

    @property
    def collected_nothing(self) -> bool:
        """True when the runner found no test to run."""
        return self.proc.returncode == NO_TESTS_COLLECTED

    @property
    def passed(self) -> bool:
        """True when every test passed, or nothing ran and that passes."""
        return self.proc.returncode == 0 or (
            self.empty_passes and self.collected_nothing
        )


def pytest(
    launcher: "list[str]",
    args: "list[str]",
    *,
    empty_passes: bool,
    **kwargs: Any,
) -> Outcome:
    """Run ``<launcher> pytest <args>`` and return its `Outcome`.

    Parameters
    ----------
    launcher : list of str
        What runs pytest: ``[python, "-m"]`` for an interpreter, or a
        ``uv run ...`` prefix whose last word is followed by ``pytest``.
    args : list of str
        pytest's own arguments.
    empty_passes : bool
        Whether exit 5 (nothing collected) is a pass. Keyword-only and
        without a default, so every caller answers it where it runs.
    **kwargs
        Passed to `subprocess.run` unchanged (``cwd``, ``env``,
        ``capture_output``, ``timeout`` ...). A ``TimeoutExpired`` propagates.

    Returns
    -------
    Outcome
        The run and its verdict.

    Examples
    --------
    >>> import sys, tempfile
    >>> empty = tempfile.mkdtemp()
    >>> run = pytest([sys.executable, "-m"], ["-q", "-p", "no:cacheprovider",
    ...     empty], empty_passes=False, capture_output=True, cwd=empty)
    >>> run.collected_nothing, run.passed
    (True, False)
    """
    cmd = [*launcher, "pytest", *args]
    return Outcome(subprocess.run(cmd, **kwargs), empty_passes)


def unittest(
    python: str,
    start: str,
    *,
    empty_passes: bool,
    **kwargs: Any,
) -> Outcome:
    """Run ``unittest discover -s <start> -v`` and return its `Outcome`.

    Through `UNITTEST_SNIPPET`, so an empty run exits `NO_TESTS_COLLECTED`
    before Python 3.12 as well as after.

    Parameters
    ----------
    python : str
        The interpreter.
    start : str
        The directory discovery starts in.
    empty_passes : bool
        Whether a run of no tests is a pass; see :func:`pytest`.
    **kwargs
        Passed to `subprocess.run` unchanged.

    Returns
    -------
    Outcome
        The run and its verdict.

    Examples
    --------
    >>> import sys, tempfile
    >>> empty = tempfile.mkdtemp()
    >>> run = unittest(sys.executable, empty, empty_passes=False,
    ...     capture_output=True)
    >>> run.proc.returncode, run.passed
    (5, False)
    """
    cmd = [python, "-c", UNITTEST_SNIPPET, start]
    return Outcome(subprocess.run(cmd, **kwargs), empty_passes)
