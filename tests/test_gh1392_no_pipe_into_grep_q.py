"""gh-1392: no workflow pipes a Python process into `grep -q`.

`pip show just-makeit | grep -q "Version: ..."` verified the installed
version in the release's pre-publish smoke. `grep -q` exits at its first
match and closes the pipe; on POSIX the writer takes SIGPIPE and dies
quietly, and `pipefail` tolerates it because grep succeeded. Windows has no
SIGPIPE, so pip raised `OSError: [Errno 22]` and exited 120 -- AFTER
installing correctly -- and the step failed.

It is a race against pip's remaining output, so it failed the py3.9 leg of
v0.77.2 while py3.12 and py3.14 passed the same run. A gate that fails on
its own plumbing is worse than none: it teaches the reader that a red
Windows smoke means nothing.

The rule is about the WRITER, not about `grep -q`: matching a file or a
shell variable is fine, and is what the fix does. Only a Python or pip
process on the left of the pipe can carry the fault, because only it turns a
closed pipe into a non-zero exit on Windows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"

# A pipeline whose LEFT side runs python/pip and whose right side is
# `grep -q`. The left side is anchored at a command position (start of the
# line, or after `then`/`do`/`;`/`&&`/`||`) so a mention inside a comment or
# a longer word ("mypython") cannot match.
_PIPE_INTO_GREP_Q = re.compile(
    r"(?:^|[;&|]|\b(?:then|do|else)\s)\s*"
    r"(?P<writer>(?:python[0-9.]*\s+-m\s+)?pip[0-9.]*\b|python[0-9.]*\b)"
    r"[^|\n]*\|\s*grep\s+-[A-Za-z]*q"
)


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    # A glob that finds nothing would make every assertion below vacuous.
    assert files, f"no workflows found under {WORKFLOWS}"
    return files


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_no_python_process_is_piped_into_grep_q(path):
    offenders = [
        (n, line.strip())
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#") and _PIPE_INTO_GREP_Q.search(line)
    ]
    assert not offenders, (
        f"{path.name} pipes a Python process into `grep -q`; on Windows the "
        "closed pipe is exit 120, not SIGPIPE (gh-1392). Read the output "
        'first: shown="$(pip show x)"; printf \'%s\\n\' "$shown" | grep -q ...'
        + "".join(f"\n  {path.name}:{n}: {text}" for n, text in offenders)
    )


def test_the_pattern_matches_the_shape_it_is_named_for():
    """The detector itself, against the four sites that failed and the fix.

    Without this, a pattern that matches nothing would pass the file above
    on every workflow, for the wrong reason.
    """
    for bad in (
        '          pip show "just-makeit" | grep -q "Version: 1.2.3"',
        '  python -m pip show "just-makeit" | grep -q "Version: 1.2.3"',
        "          then python3 -m pip list | grep -q numpy",
    ):
        assert _PIPE_INTO_GREP_Q.search(bad), bad
    for ok in (
        '          printf \'%s\\n\' "$shown" | grep -q "Version: 1.2.3"',
        '          grep -q "Version: 1.2.3" shown.txt',
        "          # pip show x | grep -q y is what gh-1392 forbids",
        "          cat log | grep -q mypython",
    ):
        assert not _PIPE_INTO_GREP_Q.search(ok), ok
