"""Every test that reads a built library's exports runs on the Windows leg.

gh-1648 taught ``tests/_exports.py`` to read a clang-cl build: the DLL's
export table and the static ``.lib``. That gates nothing unless the Windows
CI job runs the files that call it, and it runs only what its
``PROJECT_ENV_TESTS`` line names -- a narrowed list (gh-1442, gh-1376), so a
new file is off it by default. Both gh-1591's and gh-1653's symbol gates
were skipped on ``win32`` AND absent from that line: dropping the skip alone
would have moved them from skipped to never collected.

Registration-free: the importers are found by scanning ``tests/`` for an
``import _exports`` statement, so a new one is held to this without being
listed here. The job's line is read by hand, not with PyYAML, for the reason
``tests/test_own_ci_matrix.py`` gives; every parser raises rather than
returning empty, so the check cannot pass by seeing nothing.

GATE: every test file importing tests/_exports.py is named by the Windows
      CI job's PROJECT_ENV_TESTS.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
JOB = "examples-windows"

_IMPORT = re.compile(r"^(?:import _exports\b|from _exports import )", re.M)


def _job_block(text: str, job: str) -> str:
    """The lines of *job* under ``jobs:``, up to the next job key."""
    lines = text.splitlines()
    head = f"  {job}:"
    starts = [i for i, ln in enumerate(lines) if ln.rstrip() == head]
    assert len(starts) == 1, f"{head!r} occurs {len(starts)}x in {CI_YML}"
    out = []
    for ln in lines[starts[0] + 1 :]:
        if re.match(r"^  \S", ln):
            break
        out.append(ln)
    return "\n".join(out)


def windows_tests() -> "set[str]":
    """The test paths the Windows job's ``PROJECT_ENV_TESTS`` names."""
    block = _job_block(CI_YML.read_text(encoding="utf-8"), JOB)
    m = re.findall(r'PROJECT_ENV_TESTS="([^"]*)"', block)
    assert len(m) == 1, f'{JOB}: PROJECT_ENV_TESTS="..." occurs {len(m)}x'
    names = set(m[0].split())
    assert names, f"{JOB}: PROJECT_ENV_TESTS is empty"
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


def test_every_named_file_exists():
    gone = sorted(t for t in windows_tests() if not (ROOT / t).is_file())
    assert gone == [], gone
