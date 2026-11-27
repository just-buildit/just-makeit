"""gh-2170: the Makefile's PYTHON default is the base interpreter, not the venv.

The default was ``$(shell uv run --no-project python ...)``. ``uv run`` still
DISCOVERS a ``.venv`` above the working directory (gh-2163 measured this for
the test run), so inside a checkout with a dev venv, ``PYTHON`` became that
venv's interpreter. Its one consumer, ``DOCS_PREPARE``, only needs a Python,
so the dev venv was never required, and it was an accident that it was used.

Its replacement, ``uv python find --system 2>/dev/null || python3``, broke
the other way: the fallback RAN ``python3`` instead of naming it. In the docs
CI job ``uv python find --system`` honours ``.python-version`` (3.12), finds
no such system or uv-managed interpreter on the runner and exits 2; the bare
``python3`` then read its program from an empty stdin, printed nothing and
exited 0, so ``PYTHON`` was EMPTY and ``make docs-check`` executed
``scripts/copy_examples.py`` itself ("Permission denied", exit 127).

The first gate here ran only the expression's ``uv`` half, so the fallback
was never exercised. These tests expand ``PYTHON`` through ``make`` itself,
from this repo's makefiles, with stdin closed as on a CI runner: the value
make would hand a recipe, not a reconstruction of it. (A parse is well under
a second; the probe rule is the only goal, so no sub-make runs.)

GATE: PYTHON is never empty and always names a runnable Python 3, including
      when uv finds no interpreter; it is never the checkout's .venv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"

#: What an outer make (``make test``) exports. A ``PYTHON=...`` given on its
#: command line travels in MAKEFLAGS and would override the default probed.
_OUTER_MAKE = ("PYTHON", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")


def _make_python(tmp_path: Path, cwd: Path, path: str | None = None) -> str:
    """``$(PYTHON)`` exactly as make expands it, run from *cwd*.

    ``-I ROOT`` lets the Makefile's ``include standard.mk`` resolve from any
    directory. The probe rule is a FILE, not stdin, and stdin is closed: a
    fallback that RUNS an interpreter reads its program from stdin, which is
    the bug this guards. *path* replaces ``PATH`` when given.
    """
    probe = tmp_path / "value.mk"
    probe.write_text("_jm-value: ; @: $(info [$(PYTHON)])\n", encoding="utf-8")
    make = shutil.which("make")
    assert make, "make is not on PATH"
    env = {k: v for k, v in os.environ.items() if k not in _OUTER_MAKE}
    if path is not None:
        env["PATH"] = path
    r = subprocess.run(
        [
            make,
            "-s",
            "--no-print-directory",
            "-I",
            str(ROOT),
            "-f",
            str(MAKEFILE),
            "-f",
            str(probe),
            "_jm-value",
        ],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    line = r.stdout.strip().splitlines()[-1]
    assert line.startswith("[") and line.endswith("]"), r.stdout
    return line[1:-1]


def _is_python3(exe: str) -> bool:
    """Whether *exe* runs and is a Python 3."""
    out = subprocess.run(
        [exe, "-c", "import sys; print(sys.version_info[0])"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() == "3"


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
    got = _make_python(checkout_with_venv, checkout_with_venv)
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
    got = _make_python(checkout_with_venv, checkout_with_venv)
    assert _is_python3(got), f"PYTHON does not run Python 3: {got!r}"


@pytest.fixture
def uv_finds_nothing(tmp_path):
    """A PATH on which ``uv python find`` fails, as on the docs CI runner.

    ``uv`` is a stub that exits 2 -- what ``uv python find --system`` does
    there, finding no interpreter for ``.python-version`` -- and the only
    other program is a ``python3``. Deterministic on any host, where a real
    uv's answer depends on which Pythons the host happens to have.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    uv.chmod(0o755)
    python3 = shutil.which("python3")
    assert python3, "python3 is not on PATH"
    (bin_dir / "python3").symlink_to(python3)
    return str(bin_dir)


def test_the_fallback_names_a_python_when_uv_finds_none(
    tmp_path, uv_finds_nothing
):
    """The regression: the fallback RAN ``python3``, PYTHON came out empty,
    and ``make docs-check`` executed scripts/copy_examples.py itself."""
    got = _make_python(tmp_path, ROOT, path=uv_finds_nothing)
    assert got, (
        "PYTHON is EMPTY when uv finds no interpreter: the fallback ran "
        "python3 rather than naming it"
    )
    assert _is_python3(got), f"PYTHON does not run Python 3: {got!r}"
