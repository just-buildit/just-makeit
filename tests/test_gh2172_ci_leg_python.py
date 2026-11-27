"""gh-2172: a CI test leg must run the Python it names.

Each `test (<os>, <py>)` leg reaches `make test` with its interpreter given
only as `UV_PYTHON`, which `astral-sh/setup-uv` exports from the leg's
`python-version`. Every test run goes through `uv run`, which honours it.
A `--python` pin in the isolated test command, a uv default that changes, or
a `.python-version` file would run every leg on one interpreter, and the job
names and `CI passed` would read exactly as they do now.

This test is the one place that checks it. It runs inside the suite's own
interpreter, so it sees what the leg actually runs. It needs no change to
ci.yml: `UV_PYTHON` is already on the leg.

Off CI there is no leg, so `UV_PYTHON` is unset. The test then passes
without a skip, because a skip not on the allow-list turns `make test` red for
every developer. On CI a missing `UV_PYTHON` fails instead, so a leg without
one cannot pass vacuously.

`.python-version` at the root (tracked, `3.12`) does not redirect a leg: uv
gives `UV_PYTHON` precedence over it, and setup-uv sets `UV_PYTHON` per leg.
"""

import os
import re
import sys
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"
_VERSION = re.compile(r"^(\d+)\.(\d+)")


def test_the_isolated_test_command_pins_no_interpreter():
    """The pin source that overrides a leg's UV_PYTHON is `--python` in the
    isolated test command. Read from the Makefile, so it runs on every box,
    CI or not: no skip, which a developer's `make test` would count against
    the skip allow-list (gh-2172 review)."""
    text = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(r"^PYTEST_ISOLATED\s*=\s*(.*?)(?=^\S|\Z)", text, re.M | re.S)
    assert m, "the Makefile has no PYTEST_ISOLATED"
    assert "--python" not in m.group(1), (
        "PYTEST_ISOLATED pins an interpreter, so every leg runs that one, "
        "whatever its UV_PYTHON names"
    )


def test_a_ci_leg_runs_the_python_it_names():
    want = os.environ.get("UV_PYTHON", "")
    if not want:
        # A leg without UV_PYTHON runs on whatever uv finds: the matrix
        # would be silently one interpreter. Refused on CI, not skipped.
        # Off CI there is no leg to check, and the test passes: a skip
        # here would be unlisted in the allow-list and turn `make test` red.
        assert os.environ.get("GITHUB_ACTIONS") != "true", (
            "GITHUB_ACTIONS is set but UV_PYTHON is not: setup-uv did not "
            "name this leg's Python, so the leg would run uv's default"
        )
        return
    m = _VERSION.match(want)
    assert m, f"UV_PYTHON={want!r} is not a version, so it names no Python"
    assert sys.version_info[:2] == (int(m[1]), int(m[2])), (
        f"this leg names Python {want} but the suite runs "
        f"{sys.version_info.major}.{sys.version_info.minor}: a --python "
        f"pin or another uv default is overriding the leg's interpreter"
    )
