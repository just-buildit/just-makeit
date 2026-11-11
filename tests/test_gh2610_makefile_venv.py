"""gh-2610: the generated Makefile builds against the project's ``.venv``,
and installs into a venv that has no pip.

Two halves, one issue. A user built a project with ``make``, which took the
first ``python`` on ``PATH``, while Visual Studio built the same project
through ``CMakePresets.json``, which names ``.venv`` (gh-814): one project,
two interpreters. And once that ``.venv`` was uv's -- ``uv sync`` makes one,
with no pip by design -- every ``$(PYTHON) -m pip install`` the Makefile
runs (the gh-824 numpy repair, the pytest install) ended in ``No module
named pip``.

So:

- **the interpreter**: ``PYTHON=`` on the command line, then
  ``JUST_BUILDIT_PYTHON``, then the project's ``.venv`` when it exists,
  then ``PATH`` -- in both backends' Makefiles and on both arms of the cmake
  one, read off ``make -n``'s configure line, not the template's text;
- **the install**: every recipe line that installs goes through the one
  ``$(PY_INSTALL)`` (``_new.PY_INSTALL``), which runs pip when the
  interpreter has it, ``uv pip install --python <it>`` (pip's
  ``--force-reinstall`` spelled ``--reinstall``) when it does not, and with
  neither fails naming both commands. Run here against a stub pip and a stub
  uv, so what each branch EXECUTES is the assertion.

GATE: ``make`` uses the project's ``.venv`` when it exists, and no
      generated recipe installs through ``-m pip`` alone.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli


def _new(base: Path, name: str, *flags: str) -> Path:
    r = run_cli("new", name, "--object", "g", *flags, cwd=base)
    assert r.returncode == 0, r.stdout + r.stderr
    return base / name


@pytest.fixture(scope="module")
def cmake_proj(tmp_path_factory) -> Path:
    return _new(tmp_path_factory.mktemp("gh2610"), "vp", "--pytest")


@pytest.fixture(scope="module")
def make_proj(tmp_path_factory) -> Path:
    return _new(
        tmp_path_factory.mktemp("gh2610m"),
        "vm",
        "--pytest",
        "--build-system",
        "make",
    )


def _env(**extra: str) -> dict:
    """The caller's environment without what picks ``$(PYTHON)``.

    conftest sets ``JUST_BUILDIT_PYTHON`` for each xdist worker, and it
    outranks ``.venv``, so it goes unless a case sets it.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("JUST_BUILDIT_PYTHON", "PYTHON", "MAKEFLAGS")
    }
    env.update(extra)
    return env


def _configure_python(proj: Path, *args: str, **env: str) -> str:
    """The ``-DPython3_EXECUTABLE=`` the cmake Makefile would configure with."""
    r = subprocess.run(
        ["make", "-n", "build/CMakeCache.txt", *args],
        cwd=proj,
        env=_env(**env),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    found = re.findall(r"-DPython3_EXECUTABLE=(\S+)", r.stdout)
    assert len(found) == 1, r.stdout
    return found[0]


def _venv(proj: Path, sub: str) -> Path:
    """A ``.venv`` interpreter at *sub*; a real one, so make may run it."""
    py = proj / ".venv" / sub
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    return py


@pytest.fixture()
def no_venv(cmake_proj):
    yield cmake_proj
    subprocess.run(["rm", "-rf", str(cmake_proj / ".venv")], check=True)


class TestTheInterpreter:
    def test_without_a_venv_it_is_path(self, no_venv):
        got = _configure_python(no_venv)
        assert not got.startswith(str(no_venv)), got

    def test_a_venv_is_used(self, no_venv):
        py = _venv(no_venv, "bin/python")
        assert _configure_python(no_venv) == str(py)

    def test_just_buildit_python_outranks_the_venv(self, no_venv):
        _venv(no_venv, "bin/python")
        got = _configure_python(no_venv, JUST_BUILDIT_PYTHON="/jb/python")
        assert got == "/jb/python"

    def test_python_on_the_command_line_outranks_all(self, no_venv):
        _venv(no_venv, "bin/python")
        got = _configure_python(
            no_venv, "PYTHON=/cli/python", JUST_BUILDIT_PYTHON="/jb/python"
        )
        assert got == "/cli/python"

    def test_the_windows_arm_uses_scripts_python_exe(self, no_venv):
        """``OS=Windows_NT`` selects the cmake.exe arm; with a venv there,
        ``$(or ...)`` stops before the ``$(shell python ...)`` that would
        need cmd.exe, so ``-n`` can read it here."""
        py = _venv(no_venv, "Scripts/python.exe")
        assert _configure_python(no_venv, "OS=Windows_NT") == str(py)

    def test_the_make_backend_uses_the_venv(self, make_proj):
        py = _venv(make_proj, "bin/python")
        try:
            r = subprocess.run(
                ["make", "-n", "all"],
                cwd=make_proj,
                env=_env(),
                capture_output=True,
                text=True,
            )
        finally:
            subprocess.run(["rm", "-rf", str(make_proj / ".venv")])
        assert r.returncode == 0, r.stdout + r.stderr
        assert f'{py} -c "' in r.stdout, r.stdout


def _recipes(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("\t")]


class TestTheInstall:
    @pytest.fixture(params=["cmake", "make"])
    def makefile(self, request, cmake_proj, make_proj) -> str:
        proj = cmake_proj if request.param == "cmake" else make_proj
        return (proj / "Makefile").read_text(encoding="utf-8")

    def test_every_install_goes_through_py_install(self, makefile):
        installs = [
            ln for ln in _recipes(makefile) if re.search(r"pip|PY_INSTALL", ln)
        ]
        # numpy's repair (both arms of the cmake one) and pytest's.
        assert len(installs) >= 2, installs
        for ln in installs:
            assert "-m pip install" not in ln, ln
            assert '-c "$(PY_INSTALL)"' in ln, ln
        assert any("numpy" in ln for ln in installs), installs
        assert any("pytest" in ln for ln in installs), installs

    @staticmethod
    def _run(makefile: str, tmp_path: Path, path: str, pythonpath: str = ""):
        m = re.search(r"^PY_INSTALL = (.+)$", makefile, re.M)
        assert m, "PY_INSTALL is not defined"
        # -S: no site-packages, so no real pip; PYTHONPATH still applies.
        return subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                m.group(1),
                "--force-reinstall",
                "numpy",
            ],
            capture_output=True,
            text=True,
            env={"PATH": path, "PYTHONPATH": pythonpath},
        )

    def test_with_pip_it_is_pip(self, makefile, tmp_path):
        log = tmp_path / "pip.log"
        pkg = tmp_path / "stub" / "pip"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "__main__.py").write_text(
            f"import sys; open({str(log)!r}, 'w').write(' '.join(sys.argv[1:]))"
        )
        r = self._run(makefile, tmp_path, str(tmp_path), str(pkg.parent))
        assert r.returncode == 0, r.stderr
        assert log.read_text() == "install --force-reinstall numpy"

    def test_without_pip_it_is_uv(self, makefile, tmp_path):
        log = tmp_path / "uv.log"
        bin_ = tmp_path / "bin"
        bin_.mkdir()
        uv = bin_ / "uv"
        uv.write_text(f'#!/bin/sh\necho "$@" > {log}\n')
        uv.chmod(0o755)
        r = self._run(makefile, tmp_path, str(bin_))
        assert r.returncode == 0, r.stderr
        assert log.read_text().split() == [
            "pip",
            "install",
            "--python",
            sys.executable,
            "--reinstall",
            "numpy",
        ]

    def test_with_neither_it_names_both_commands(self, makefile, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        r = self._run(makefile, tmp_path, str(empty))
        assert r.returncode != 0
        assert "No module named pip" not in r.stderr, r.stderr
        assert f"{sys.executable} -m pip install" in r.stderr, r.stderr
        assert f"uv pip install --python {sys.executable}" in r.stderr
