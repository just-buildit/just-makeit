"""gh-1898: `build` follows the interpreter it is asked for.

The PEP 517 frontend runs `just-build` in an isolated environment, which it
then deletes. `just-build` configures the shared `build/` with that
environment's interpreter, so the cache keeps paths inside a directory that no
longer exists (a numpy include dir, here). The next `make` then fails on a
header that is gone, and `build` never reconfigured because `CMakeCache.txt`
was present.

GATE: `make build` configures against the interpreter it is given. After a
build through another interpreter, the cache names that one, and a build
through a third, after the second is deleted, succeeds. Sabotage proof: removing
the cache-check line from the make template turns it red.

The two interpreters are wrapper scripts that exec the test's own Python, the
same pattern `test_gh1915_1833_1841_bench` uses, so they have numpy and cmake
finds it through either.
"""

# gh-1591: this file's expectations spell jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _compilers import default_cc  # noqa: E402
from _jmrun import run_cli  # noqa: E402

_NEEDS_BUILD = pytest.mark.skipif(
    shutil.which("make") is None
    or shutil.which("cmake") is None
    or default_cc() is None,
    reason="the build gate needs make, cmake and a C compiler",
)


def _wrapper(dir: Path, name: str) -> Path:
    """A python that is this test's interpreter, under its own path."""
    dir.mkdir(parents=True, exist_ok=True)
    p = dir / name
    p.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def _cached_python(build: Path) -> str:
    """The interpreter the cache was configured with."""
    for line in (
        (build / "CMakeCache.txt").read_text(encoding="utf-8").splitlines()
    ):
        if line.startswith("Python3_EXECUTABLE:"):
            return line.split("=", 1)[1].strip()
    raise AssertionError("CMakeCache.txt names no Python3_EXECUTABLE")


def _make(root: Path, *args: str):
    """`make` with the interpreter passed on the command line, so the test
    never depends on whatever `python3` the box happens to have."""
    import subprocess

    return subprocess.run(
        ["make", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env={**os.environ, "NPROC": "2"},
    )


@_NEEDS_BUILD
def test_build_configures_against_the_interpreter_it_is_given(
    tmp_path: Path,
) -> None:
    """Build through A, then through B: the cache must now name B."""
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    a = _wrapper(tmp_path / "a", "python")
    b = _wrapper(tmp_path / "b", "python")

    first = _make(root, f"PYTHON={a}", "build")
    assert first.returncode == 0, first.stdout + first.stderr
    assert _cached_python(root / "build") == str(a)

    second = _make(root, f"PYTHON={b}", "build")
    assert second.returncode == 0, second.stdout + second.stderr
    assert _cached_python(root / "build") == str(b)


@_NEEDS_BUILD
def test_a_deleted_interpreter_does_not_fail_the_next_build(
    tmp_path: Path,
) -> None:
    """The reported failure: the interpreter a build was run through is gone.
    The next `build` through a live one must reconfigure, not reuse its
    paths."""
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    a = _wrapper(tmp_path / "a", "python")
    b = _wrapper(tmp_path / "b", "python")

    built = _make(root, f"PYTHON={a}", "build")
    assert built.returncode == 0, built.stdout + built.stderr
    a.unlink()

    after = _make(root, f"PYTHON={b}", "build")
    assert after.returncode == 0, after.stdout + after.stderr
    assert _cached_python(root / "build") == str(b)
