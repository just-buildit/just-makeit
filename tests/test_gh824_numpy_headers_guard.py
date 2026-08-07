"""gh-824: the generated build must check numpy's HEADERS, not its importability.

`find_package(Python3 ... NumPy)` resolves `Python3_NumPy_INCLUDE_DIRS` from
`numpy.get_include()`. A numpy whose package imports while that directory is
absent therefore satisfies an `import numpy` guard and fails afterwards — as
`Could NOT find Python3_NumPy_INCLUDE_DIRS`, or, when the path is only read at
generate time, as `Imported target "Python3::NumPy" includes non-existent
path`, or (on the simple backend, which passes the directory as `-I`) as
`numpy/arrayobject.h: No such file`. Three of the four symptom shapes gh-811
catalogued come from this one condition.

The second half matters as much as the first: in that state numpy IS
installed, so the old remedy — `pip install numpy` — is a no-op and cannot
repair what the guard failed to detect.

These tests pin the predicate and the remedy, and run the predicate against a
numpy whose `get_include()` points nowhere, which is the state itself.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as R
from just_makeit._new import run as new_run


@pytest.fixture()
def makefile(tmp_path) -> str:
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root, ["gain"], [("g", "float", "0.0f")])
    return (root / "Makefile").read_text()


def _predicate(text: str, name: str) -> str:
    """Pull one `NUMPY_HEADERS_* = <python expr>` out of a Makefile."""
    m = re.search(rf"^{name}\s*=\s*(.+)$", text, re.M)
    assert m, f"{name} is not defined"
    return m.group(1).strip()


def _run(expr: str, tmp_path: Path, include: str | None):
    """Run *expr* against a stub numpy whose get_include() returns *include*.

    A stub rather than the real numpy: the condition under test is "the
    directory is missing", and the honest way to produce it is a numpy that
    reports a path that is not there — not deleting the headers out of the
    interpreter running the suite.
    """
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    target = include if include is not None else str(tmp_path / "gone")
    (stub / "numpy.py").write_text(
        f"def get_include():\n    return {target!r}\n"
    )
    return subprocess.run(
        [sys.executable, "-c", expr],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(stub), "PATH": "/usr/bin:/bin"},
    )


class TestThePredicate:
    def test_it_tests_the_directory_not_the_import(self, makefile):
        """`import numpy` alone is the bug. Guard against a revert."""
        expr = _predicate(makefile, "NUMPY_HEADERS_OK")
        assert "get_include" in expr
        assert "isdir" in expr

    def test_missing_headers_are_a_failure(self, makefile, tmp_path):
        expr = _predicate(makefile, "NUMPY_HEADERS_OK")
        assert _run(expr, tmp_path, include=None).returncode != 0

    def test_present_headers_are_a_pass(self, makefile, tmp_path):
        expr = _predicate(makefile, "NUMPY_HEADERS_OK")
        real = tmp_path / "inc"
        real.mkdir()
        assert _run(expr, tmp_path, include=str(real)).returncode == 0

    def test_an_importable_numpy_alone_does_not_satisfy_it(
        self, makefile, tmp_path
    ):
        """The whole point: the stub imports fine and still fails."""
        expr = _predicate(makefile, "NUMPY_HEADERS_OK")
        r = _run("import numpy", tmp_path, include=None)
        assert r.returncode == 0, "sanity: the stub is importable"
        assert _run(expr, tmp_path, include=None).returncode != 0


class TestTheVerifyNamesThePath:
    def test_it_fails_and_prints_the_directory(self, makefile, tmp_path):
        expr = _predicate(makefile, "NUMPY_HEADERS_VERIFY")
        missing = str(tmp_path / "gone")
        r = _run(expr, tmp_path, include=missing)
        assert r.returncode != 0
        assert missing in r.stderr, "the message must name the directory"
        assert "gh-824" in r.stderr

    def test_it_is_silent_when_the_headers_are_there(self, makefile, tmp_path):
        expr = _predicate(makefile, "NUMPY_HEADERS_VERIFY")
        real = tmp_path / "inc"
        real.mkdir()
        r = _run(expr, tmp_path, include=str(real))
        assert r.returncode == 0
        assert r.stderr == ""


class TestTheRemedyCanActuallyRepair:
    """A plain `pip install numpy` is a no-op when numpy is already there."""

    def test_the_cmake_backend_reinstalls(self, makefile):
        # Recipe lines only. The prose above them explains why a plain install
        # is a no-op, so a whole-file substring search matches the comment and
        # passes for the wrong reason.
        recipes = [
            ln
            for ln in makefile.splitlines()
            if "pip install" in ln and not ln.lstrip().startswith("#")
        ]
        assert recipes, "nothing installs numpy at all"
        assert all("--force-reinstall" in ln for ln in recipes), recipes

    def test_the_verify_runs_after_the_install(self, makefile):
        """Unconditionally, so an environment the reinstall did not fix says
        so here rather than at the cmake error two steps later."""
        ok = makefile.index("NUMPY_HEADERS_OK)")
        verify = makefile.index("NUMPY_HEADERS_VERIFY)")
        assert ok < verify


class TestBothBackends:
    def test_the_simple_backend_guards_its_dash_capital_i_too(self):
        """It interpolates get_include() straight into `-I`, so a missing
        directory there is `numpy/arrayobject.h: No such file`."""
        comp = R.MAKEFILE_SIMPLE_COMPONENT
        assert "NUMPY_HEADERS_OK" in comp
        assert "NUMPY_HEADERS_VERIFY" in comp
        assert "--force-reinstall" in comp
        # ...and the variables it references are defined by the parent it is
        # spliced into, not left dangling.
        assert "NUMPY_HEADERS_OK" in R.MAKEFILE_SIMPLE
        assert "NUMPY_HEADERS_VERIFY" in R.MAKEFILE_SIMPLE

    def test_the_windows_arm_is_guarded_as_well(self, makefile):
        """Both arms of the `ifeq ($(OS), Windows_NT)` run cmake."""
        assert makefile.count("NUMPY_HEADERS_OK)") == 2
        assert makefile.count("NUMPY_HEADERS_VERIFY)") == 2
