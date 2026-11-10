"""Every test that reads a built library's exports runs on the Windows leg.

gh-1648 taught ``tests/_exports.py`` to read a clang-cl build: the DLL's
export table and the static ``.lib``. That gates nothing unless the Windows
CI job runs the files that call it, and it runs only what its
``PROJECT_ENV_TESTS`` line names -- a narrowed list (gh-1442, gh-1376), so a
new file was off it by default. Both gh-1591's and gh-1653's symbol gates
were skipped on ``win32`` AND absent from that line: dropping the skip alone
would have moved them from skipped to never collected.

gh-1570 removed the narrowing: the job's main step runs ``make
test-examples`` with no ``PROJECT_ENV_TESTS`` of its own, so it runs the
Makefile's one declaration. What a step runs is read from the step as GitHub
reads it -- its own ``PROJECT_ENV_TESTS=`` words if it narrows, else the
list ``make`` expands -- so a narrowing put back is caught by the second
gate below, and the export readers stay held by the first either way.

Registration-free: the importers are found by scanning ``tests/`` for an
``import _exports`` statement, and the full list is what ``make -n``
expands, so neither is listed here. Every parser raises rather than
returning empty, so the check cannot pass by seeing nothing.

GATE: every test file importing tests/_exports.py is run by the Windows
      CI job.
GATE: the Windows CI job runs every file of the Makefile's
      PROJECT_ENV_TESTS (gh-1570).
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
JOB = "examples-windows"

_IMPORT = re.compile(r"^(?:import _exports\b|from _exports import )", re.M)


_TEST_PATH = re.compile(r"\btests/test_\w+\.py\b")


def project_env_tests() -> "set[str]":
    """The Makefile's ``PROJECT_ENV_TESTS``, as ``make`` expands it.

    Read off the recipe ``make -n test-examples`` would run, so the one
    declaration is the only list; nothing here restates it.
    """
    proc = subprocess.run(
        ["make", "-nrR", "--no-print-directory", "test-examples"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    names = set(_TEST_PATH.findall(proc.stdout))
    assert "tests/test_examples.py" in names, proc.stdout
    return names


def windows_tests() -> "set[str]":
    """Every test path a step of the Windows job runs.

    Every step's, not one: gh-1839 runs one file in a step of its own, with
    the runner's git configuration, and a file either step runs is run. A
    ``make test-examples`` step with no ``PROJECT_ENV_TESTS=`` word runs
    the Makefile's list.
    """
    job = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"][JOB]
    runs = [
        shlex.split(str(s["run"]))
        for s in job["steps"]
        if str(s.get("run", "")).lstrip().startswith("make test-examples")
    ]
    assert runs, f"{JOB}: no step runs `make test-examples`"
    names: "set[str]" = set()
    for words in runs:
        narrowed = [
            w.split("=", 1)[1]
            for w in words
            if w.startswith("PROJECT_ENV_TESTS=")
        ]
        if narrowed:
            names |= {n for line in narrowed for n in line.split()}
        else:
            names |= project_env_tests()
    assert names, f"{JOB}: runs no test file"
    return names


def export_readers() -> "set[str]":
    """Every ``tests/test_*.py`` that imports ``_exports``."""
    return {
        p.relative_to(ROOT).as_posix()
        for p in sorted((ROOT / "tests").glob("test_*.py"))
        if _IMPORT.search(p.read_text(encoding="utf-8"))
    }


def test_the_parsers_see_something():
    readers = export_readers()
    assert "tests/test_gh1591_c_prefix_nm.py" in readers, readers
    assert "tests/test_examples.py" in windows_tests()


def test_every_export_reader_runs_on_windows():
    missing = sorted(export_readers() - windows_tests())
    assert missing == [], (
        f"these read a library's exports through tests/_exports.py, which "
        f"has a Windows reader, but the {JOB} job in {CI_YML.name} does "
        f"not run them -- add them to its PROJECT_ENV_TESTS: {missing}"
    )


def test_the_windows_job_runs_every_project_env_test():
    """gh-1570: the leg is not narrowed. A tool gate that cannot hold on
    Windows is registered with the suite's skip gate, with its reason; it
    is not left off this job, where nothing would say it is missing."""
    missing = sorted(project_env_tests() - windows_tests())
    assert missing == [], (
        f"the {JOB} job in {CI_YML.name} does not run these files of the "
        f"Makefile's PROJECT_ENV_TESTS: {missing}. Run `make test-examples` "
        f"there with no PROJECT_ENV_TESTS of its own."
    )


def test_every_named_file_exists():
    gone = sorted(t for t in windows_tests() if not (ROOT / t).is_file())
    assert gone == [], gone
