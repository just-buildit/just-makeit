"""gh-1374: a test drives jm's CLI in THIS process, not in a child.

52 files each spawned `python -c "from just_makeit._cli import main;
main()"` -- ~3300 children per run, 76% of every child the suite starts.
Under coverage each one costs ~7 ms to start, ~43 ms more to start coverage,
and a `.coverage.*` file the run must combine: 227 s, 45% of a 503 s
coverage run, measured 2026-09-19.

None of them needed a process. `tests/_jmrun.run_cli` gives the same
isolation (argv, cwd, stdin, `SystemExit`) and the same result shape, and
what `main()` does is then measured natively by the parent.

This gate keeps the pattern from coming back. It is registration-free: a
new test file is covered the moment it exists, and a file that genuinely
needs a child says so HERE, with its reason, rather than in a comment
nobody greps.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).parent

# What a child is HANDED, not what the file mentions. The 52 copies wrote
# `python -c "from just_makeit._cli import main; main()"` on one line and
# gh-1387 spells the same thing across several, so a one-line needle would
# have called gh-1387 clean. A plain substring scan is wrong the other way:
# test_gh543_container_property.py imports the CLI at module level (the
# in-process form this gate WANTS) and separately spawns a child that
# imports the built extension -- innocent on both counts.
#
# So: the CLI must appear inside a STRING, which is how source reaches a
# child, rather than in an import statement, which is how it reaches this
# process.
THE_CLI = "just_makeit._cli import main"


def _drives_the_cli_in_a_child(text: str) -> bool:
    """True when a string literal in *text* hands jm's CLI to a child."""
    return any(
        isinstance(node.value, str) and THE_CLI in node.value
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Constant)
    )


# A real child, and why. Each entry is a claim that in-process would test
# something OTHER than what the file is about.
NEEDS_A_CHILD = {
    "test_gh1387_utf8_stdio.py": (
        "the subject IS a process's stdio encoding -- in-process it would "
        "assert about io.StringIO, not about what Windows does to a pipe"
    ),
}

# This file names both halves of what it looks for, so it matches itself.
SELF = Path(__file__).name


def _test_files() -> list[Path]:
    files = sorted(TESTS.glob("test_*.py"))
    # A glob that finds nothing would make this file vacuous.
    assert len(files) > 100, f"only {len(files)} test files found in {TESTS}"
    return files


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.name)
def test_the_cli_is_not_driven_through_a_subprocess(path):
    if path.name == SELF:
        pytest.skip("names the pattern in order to detect it")
    if path.name in NEEDS_A_CHILD:
        pytest.skip(f"allow-listed: {NEEDS_A_CHILD[path.name]}")
    assert not _drives_the_cli_in_a_child(path.read_text()), (
        f"{path.name} drives jm's CLI in a subprocess. Use "
        "`from _jmrun import run_cli` -- same argv/cwd/stdin isolation, same "
        "returncode/stdout/stderr, and coverage sees it (gh-1374). If it "
        "truly needs a child, add it to NEEDS_A_CHILD here with the reason."
    )


@pytest.mark.parametrize("name,reason", sorted(NEEDS_A_CHILD.items()))
def test_each_allow_listed_file_still_exists_and_still_spawns(name, reason):
    """An allow-list entry that no longer applies is a licence nobody revoked.

    Both halves: the file must exist, and it must still spawn. A renamed or
    since-converted file would otherwise keep its exemption forever, and the
    next file to take that name would inherit it silently.
    """
    path = TESTS / name
    assert path.exists(), f"{name} is allow-listed but does not exist"
    assert _drives_the_cli_in_a_child(path.read_text()), (
        f"{name} no longer spawns the CLI -- drop it from NEEDS_A_CHILD"
    )
    assert reason.strip(), f"{name} must say why"


def test_the_detector_matches_both_spellings_and_not_innocent_files():
    """Built from parts, so this test cannot be satisfied by the literals above.

    The one-line form the 52 files used, the multi-line form gh-1387 uses,
    and the shape that must stay clean: a module-level CLI import beside a
    child that runs something else.
    """
    one_line = 'x = "from ' + 'just_makeit._cli import main; main()"'
    multi_line = (
        'x = """\nimport sys\nfrom '
        + 'just_makeit._cli import main\nmain()\n"""'
    )
    assert _drives_the_cli_in_a_child(one_line), "the 52 files' spelling"
    assert _drives_the_cli_in_a_child(multi_line), "gh-1387's spelling"
    # An import statement is this process, not a child -- gh543's shape.
    assert not _drives_the_cli_in_a_child(
        "from just_makeit._cli import main as cli_main\n"
        "subprocess.run([sys.executable, '-c', 'import proj.rdr'])"
    )
