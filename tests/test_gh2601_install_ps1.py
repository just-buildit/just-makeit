"""gh-2601: jm's installers on Windows -- install.ps1, and a refusal.

A Windows reader was told to ``py -m pip install just-makeit`` or ``uv
tool install just-makeit`` and then run ``jm``. Neither works on a fresh
machine: pip puts ``jm.exe`` in a Python ``Scripts`` folder that is not on
PATH, and uv's tool folder needs ``uv tool update-shell`` and a new prompt.
And ``install.sh``, run from Git Bash, read the package manager as
``unknown`` and carried on, checking no compiler (the issue), as did
``jm-install-deps``.

Now Windows has its own installer, ``install.ps1`` -- ``irm
<site>/install.ps1 | iex`` -- and both bash installers stop on Windows
naming what to run instead. What only Windows can answer -- ``iex`` in
both PowerShell editions, ``jm`` on PATH afterwards, nothing left behind,
a second run a no-op -- is ``tests/install-ps1.windows.ps1``, which CI's
Windows job runs; this file holds what every platform can:

- each bash installer, under a ``uname`` that answers as Git Bash, MSYS2
  or Cygwin does, exits 1 naming the install.ps1 line and installs
  nothing;
- install.ps1's Python floor is pyproject's ``requires-python``, the one
  source ``tests/_pyfloor.py`` reads, as install.sh's is held by
  ``test_gh1916_install_sh_floor.py``;
- install.ps1 is ASCII, because Windows PowerShell 5.1 reads a BOM-less
  script in the ANSI code page;
- every function install.ps1 defines is one its ``finally`` removes, so
  ``iex`` leaves none in the caller's session -- derived from the file, so
  a helper added later is held too;
- every ``tests/*.windows.ps1`` runs in CI's Windows job under BOTH pwsh 7
  and Windows PowerShell 5.1, and that job points the Visual Studio
  examples' install line at the checkout.

The site publishing install.ps1 is held by
``tests/test_aliases_toml_published.py``.

GATE: the bash installers refuse Windows naming install.ps1; install.ps1's
      floor, encoding and cleanup hold; its Windows test runs in both
      editions in CI.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
import yaml
from _installers import INSTALLERS, ROOT
from _pyfloor import python_floor

INSTALL_PS1 = ROOT / "install.ps1"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

#: The job that builds the examples on Windows, where the PowerShell tests
#: run too.
WINDOWS_JOB = "examples-windows"

#: The one-liner every refusal names.
ONE_LINER = "irm https://just-buildit.github.io/just-makeit/install.ps1 | iex"

#: What ``uname`` prints under Git Bash, MSYS2 and Cygwin.
WINDOWS_UNAMES = (
    "MINGW64_NT-10.0-26100",
    "MSYS_NT-10.0-26100",
    "CYGWIN_NT-10.0-26100",
)

BASH = shutil.which("bash")


@pytest.mark.skipif(not BASH, reason="the installers are bash scripts")
@pytest.mark.parametrize("uname", WINDOWS_UNAMES)
@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
@pytest.mark.parametrize("args", [(), ("--check",)], ids=["run", "check"])
def test_bash_installers_stop_on_windows(tmp_path, script, uname, args):
    # A PATH holding only `uname`: a refusal that came after any probe or
    # package manager would fail on a missing command instead, so this
    # also proves the refusal comes first.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "uname"
    stub.write_text(f"#!/bin/sh\necho {uname}\n", encoding="utf-8")
    stub.chmod(0o755)
    res = subprocess.run(
        [BASH, str(script), *args, str(tmp_path / "venv")],
        cwd=tmp_path,
        env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    said = res.stdout + res.stderr
    assert res.returncode == 1, said
    assert "does not install on Windows" in res.stderr, said
    assert ONE_LINER in res.stderr, said
    assert not (tmp_path / "venv").exists(), said


def _ps1_text() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def test_install_ps1_floor_is_pyprojects():
    floors = re.findall(r"^\$JmPyFloor = '([0-9.]+)'$", _ps1_text(), re.M)
    assert len(floors) == 1, floors
    want = ".".join(str(p) for p in python_floor())
    assert floors[0] == want, (
        f"install.ps1 holds the floor {floors[0]}, pyproject {want}"
    )


def test_install_ps1_is_ascii():
    bad = [
        (n, line)
        for n, line in enumerate(_ps1_text().splitlines(), 1)
        if not line.isascii()
    ]
    assert not bad, bad


def test_install_ps1_removes_every_function_it_defines():
    text = _ps1_text()
    defined = set(re.findall(r"^function ([\w-]+)", text, re.M))
    assert "Install-JustMakeit" in defined, defined
    # The cleanup is the `finally` after the define-only guard; the names
    # it removes are the quoted words in it.
    guard = "Get-Variable -Name JmInstallDefineOnly"
    assert text.count(guard) == 1, "install.ps1 lost its define-only guard"
    # A dot-source guard would turn jbx's dot-sourced run into a no-op.
    assert "$MyInvocation.InvocationName -eq '.'" not in text
    tail = text.split(guard, 1)[1]
    assert "finally" in tail, tail
    removed = set(re.findall(r"'([\w-]+)'", tail.split("finally", 1)[1]))
    assert defined <= removed, sorted(defined - removed)
    variables = set(re.findall(r"^\$(\w+) = ", text, re.M))
    gone = re.search(r"Remove-Variable -Name ([\w, ]+)", tail)
    assert gone, tail
    named = {v.strip() for v in gone.group(1).split(",")}
    assert variables <= named, sorted(variables - named)


def test_vs_examples_install_line_is_the_runners():
    # The page shows `jbx just-makeit:install`; the runner maps exactly
    # that line to install.ps1 run directly, and says so. The TL;DR, the
    # step and the runner must name one line, or the page and the run
    # drift apart silently.
    from just_makeit import _vsexample

    examples = ROOT / "src" / "just_makeit" / "examples"
    pages = sorted(examples.glob("visual_studio_*/.steps"))
    assert pages, examples
    assert _vsexample._INSTALL_PS1.endswith("/install.ps1")
    for steps in pages:
        first = _vsexample.commands(steps / "01_scaffold.ps1")[0]
        assert " ".join(first) == _vsexample._INSTALL_LINE, (steps, first)
        header = (steps / "00_header.md").read_text(encoding="utf-8")
        tldr = header.split("```powershell\n", 1)[1].splitlines()[0]
        assert tldr == _vsexample._INSTALL_LINE, (steps, tldr)


def _windows_steps() -> "list[dict]":
    ci = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    return ci["jobs"][WINDOWS_JOB]["steps"]


def test_every_windows_ps1_test_runs_in_both_editions():
    tests = sorted(p.name for p in (ROOT / "tests").glob("*.windows.ps1"))
    assert "install-ps1.windows.ps1" in tests, tests
    runs = [s.get("run", "") for s in _windows_steps()]
    for name in tests:
        for editions in (
            f"pwsh -NoProfile -File tests/{name}",
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
            f"tests/{name}",
        ):
            assert editions in runs, f"{WINDOWS_JOB} does not run `{editions}`"


def test_windows_examples_install_from_the_checkout():
    # The Visual Studio examples' first line is the one-liner; in CI it
    # must run the checkout's install.ps1 and install a wheel of the tree,
    # or the job tests the published script and PyPI's release.
    steps = _windows_steps()
    names = [s.get("name", "") for s in steps]
    build = names.index("Build the wheel install.ps1 installs")
    examples = names.index("Run example tests")
    assert build < examples, names
    run = steps[build]["run"]
    assert "make wheel" in run, run
    for var in ("JM_INSTALL_PS1", "JM_INSTALL_FROM"):
        assert f"{var}=" in run and "GITHUB_ENV" in run, (var, run)
