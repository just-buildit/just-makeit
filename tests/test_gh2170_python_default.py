"""gh-2170: the Makefile's PYTHON default is the base interpreter, not the venv.

The default was ``$(shell uv run --no-project python ...)``. ``uv run`` still
DISCOVERS a ``.venv`` above the working directory (gh-2163 measured this for
the test run), so inside a checkout with a dev venv, ``PYTHON`` became that
venv's interpreter. Its one consumer, ``DOCS_PREPARE``, only needs a Python,
so the dev venv was never required, and it was an accident that it was used.

The gate does not call ``make``: parsing this Makefile runs for minutes here
(``include standard.mk`` and its sub-makes). It takes the ``$(shell ...)`` text
out of the Makefile itself, so it tests the expression make would run, and
runs it in a directory that holds a real ``.venv`` (created by ``uv venv``,
the way a dev venv is made, so ``uv`` discovers it the same way).
"""

import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"


def _python_default_shell() -> str:
    """The shell text of the Makefile's ``PYTHON ?= $(shell ...)``."""
    text = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(r"^PYTHON\s+\?=\s+\$\(shell (.*)\)\s*$", text, re.M)
    assert m, "the Makefile's PYTHON default is not a single $(shell ...)"
    return m.group(1)


def _run_in(cwd: Path) -> str:
    """The interpreter the Makefile's default resolves to, from *cwd*.

    No shell (gh-1625 refuses a test that hands a shell its program as
    text): the expression's command is split into argv and run directly. The
    `2>/dev/null || python3` tail is make's own fallback, applied here too.
    """
    expr = _python_default_shell().split("2>/dev/null")[0].strip()
    out = subprocess.run(
        shlex.split(expr),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip()


@pytest.fixture
def checkout_with_venv(tmp_path):
    """A directory shaped like a dev checkout: a project (`pyproject.toml`,
    which is what uv discovers a project by) with a stdlib venv in `.venv`."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0"\n', encoding="utf-8"
    )
    subprocess.run(
        ["uv", "venv", "-q", str(tmp_path / ".venv")],
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_the_default_is_not_the_checkouts_venv(checkout_with_venv):
    got = _run_in(checkout_with_venv)
    assert got, "the PYTHON default resolved to nothing"
    # Not `.resolve()` on *got*: .venv/bin/python is a symlink to the base
    # interpreter, so resolving it drops the venv from its parents.
    venv = (checkout_with_venv / ".venv").resolve()
    assert venv not in Path(got).parents, (
        f"PYTHON defaults to the dev venv: {got}"
    )


def test_the_default_still_names_a_python(checkout_with_venv):
    """Dropping the venv must not drop the interpreter: DOCS_PREPARE needs
    one, so the default still runs."""
    got = _run_in(checkout_with_venv)
    out = subprocess.run(
        [got, "-c", "import sys; print(sys.version_info[0])"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.stdout.strip() == "3", (got, out.stderr)
