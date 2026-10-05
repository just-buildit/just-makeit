"""Run cmake-lint over generated CMake, the one way the suite does (gh-1930).

``tests/test_examples.py`` and ``tests/test_cmake_lint.py`` each carried
their own copy of this: a bare ``cmake-lint`` from PATH -- whichever
cmakelang the Python under test had installed -- and an assertion that
printed stdout alone. Two defects followed from that, and both were in both
copies:

- **The interpreter was the suite's.** cmakelang's last release (2020)
  builds its lexer from an ``re.Scanner`` with capturing groups, which
  CPython 3.15 refuses. Under 3.15 every run crashed before reading a file.
  The command now comes from the Makefile's ``CMAKE_LINT``, which runs
  cmakelang under its own pinned Python; the recipe that runs these tests
  hands it over in the environment, as ``make consumer-smoke`` hands its
  script ``JM`` (gh-1625). With no ``CMAKE_LINT`` the test FAILS: the old
  ``shutil.which`` guard returned quietly, so a missing tool passed.
- **A crash read as findings.** cmake-lint exits 1 for lint findings and 2
  for an internal error (``cmakelang/lint/__main__.py``, ``main``), and the
  traceback goes to stderr. Printing stdout alone reported the 3.15 crash as
  "found violations" with an empty list. Exit 2 is now reported as a crash,
  and every failure carries stderr.

Usage::

    from _cmakelint import check
    check(project_root.rglob("CMakeLists.txt"), "generated project")
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable

import pytest

#: The environment variable the Makefile's recipe sets (PYTEST_EXAMPLES).
ENV = "CMAKE_LINT"

#: Formatting rules are cmake-format's responsibility: C0301 line length,
#: C0307 indentation.
DISABLED = ["C0301", "C0307"]
# gh-1368: C0327 (line ending) is not checked on Windows. There an example's
# OWN CMakeLists -- written by its test with Path.write_text, as a user's
# would be -- is CRLF, which is correct for that platform. jm's generated
# files are held to LF separately, in source, by test_gh1368_lf_writes.py.
if os.name == "nt":
    DISABLED.append("C0327")

#: cmake-lint's exit status for an internal error (an exception caught in
#: ``main``); 1 is findings, or a usage error it reports on stderr. uv exits
#: 2 as well when it cannot start the tool at all.
CRASHED = 2


def command() -> list[str]:
    """The cmake-lint command the Makefile declares, as an argv prefix.

    Returns
    -------
    list of str
        ``$CMAKE_LINT`` split as a POSIX shell would, ready to have the
        arguments appended.

    Raises
    ------
    pytest.fail.Exception
        When ``CMAKE_LINT`` is unset or empty: this test was run outside the
        recipe that supplies it, and no other cmake-lint is the right one.
    """
    cmd = os.environ.get(ENV, "").strip()
    if not cmd:
        pytest.fail(
            f"{ENV} is unset, so there is no cmake-lint to run. The command "
            "is the Makefile's CMAKE_LINT -- cmakelang under its pinned "
            "Python, not this suite's (gh-1930) -- and the recipe hands it "
            "over: run `make test-examples` (narrow it with EXAMPLES_K= or "
            "PROJECT_ENV_TESTS=), or `make test` for the goldens."
        )
    return shlex.split(cmd)


def run(files: Iterable[Path]) -> subprocess.CompletedProcess:
    """Run cmake-lint over *files*, with the formatting codes disabled.

    Parameters
    ----------
    files : iterable of Path
        The CMake files to lint.

    Returns
    -------
    subprocess.CompletedProcess
        Text-mode, with stdout and stderr captured.
    """
    return subprocess.run(
        [*command(), "--disabled-codes", *DISABLED, "--"]
        + [str(f) for f in files],
        capture_output=True,
        text=True,
        timeout=600,
    )


def failure(r: subprocess.CompletedProcess, what: str) -> str:
    """Why cmake-lint's run *r* over *what* failed, or ``""`` if it passed.

    A crash and a finding are different failures and are worded apart, so
    a broken tool cannot pass for a broken project. Both streams are
    included either way: findings go to stdout, while a traceback, a usage
    error and uv's own errors go to stderr.

    Parameters
    ----------
    r : subprocess.CompletedProcess
        The result of :func:`run`.
    what : str
        What was linted, for the message ("generated project").

    Returns
    -------
    str
        The assertion message, or an empty string for exit status 0.
    """
    if r.returncode == 0:
        return ""
    if r.returncode == CRASHED:
        head = (
            f"cmake-lint crashed linting {what} (exit {r.returncode}): an "
            "internal error, or a tool that could not start -- NOT a lint "
            "finding"
        )
    elif r.returncode == 1:
        head = f"cmake-lint found violations in {what} (exit 1)"
    else:
        head = f"cmake-lint failed linting {what} (exit {r.returncode})"
    return f"{head}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"


def check(files: Iterable[Path], what: str) -> None:
    """Assert cmake-lint passes over *files*; *what* names them.

    Parameters
    ----------
    files : iterable of Path
        The CMake files to lint; there must be at least one, or the check
        would pass having read nothing.
    what : str
        What was linted, for the message.

    Raises
    ------
    AssertionError
        With :func:`failure`'s message when cmake-lint exits non-zero.
    """
    files = list(files)
    assert files, f"no CMake files to lint in {what}"
    r = run(files)
    assert r.returncode == 0, failure(r, what)
