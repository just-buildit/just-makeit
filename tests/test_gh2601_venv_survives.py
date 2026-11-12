"""gh-2601: the venv jm is installed into survives a reboot.

install.sh defaulted to ``/tmp/jm-venv`` and then linked ``~/.local/bin/jm``
into it, telling the user to keep ``~/.local/bin`` on PATH for good. Where
``/tmp`` is a tmpfs or is cleaned at boot (Arch, Fedora, ...), every shell
after the next reboot found a dangling ``jm``. ``jm-install-deps`` had the
same default.

Now each installer decides where the venv goes in ONE function, by one
rule:

- in a project root -- the current directory holds ``pyproject.toml`` or
  ``just-makeit.toml`` -- the project's ``./.venv``, linked nowhere;
- anywhere else the per-user venv under
  ``${XDG_DATA_HOME:-~/.local/share}/just-makeit/`` (install.ps1:
  ``LOCALAPPDATA\\just-makeit\\venv``), linked into ``~/.local/bin``;
- a path argument, else ``JM_VENV``, wins, and is a project's when the
  directory holding it is a project root.

install.sh's ``_jm_where`` and install-deps.sh's are one function in two
files (install.sh is fetched alone and cannot source the other); this
file holds them byte-identical, then RUNS the one function through each
file's own copy. install.ps1's ``Get-JmVenvDir`` is run on Windows by
``tests/install-ps1.windows.ps1``; here its text is held off the temp
folder. What each case does with the venv -- linking, the jbx marker --
is ``tests/test_gh2601_install_sh_links.py``.

GATE: no installer's default venv is under a temporary directory; the
      project-root and per-user cases resolve as stated, in both bash
      copies.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from _installers import INSTALL_DEPS, INSTALL_SH, INSTALLERS, ROOT, functions

INSTALL_PS1 = ROOT / "install.ps1"

BASH = shutil.which("bash")

#: The function each bash installer decides the venv in.
WHERE = "_jm_where"

#: A HOME that is not under any temporary directory, so a default derived
#: from it can be told apart from one under /tmp.
HOME = "/home/jm-survives-user"


def _temp_dirs(tmpdir: Path) -> "set[str]":
    """Every temporary directory a default must not be under."""
    return {"/tmp", "/var/tmp", tempfile.gettempdir(), str(tmpdir)}


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def _where(
    script: Path,
    cwd: Path,
    *args: str,
    env: "dict[str, str] | None" = None,
) -> "tuple[str, str]":
    """``(mode, venv)`` from *script*'s own ``_jm_where``, run in *cwd*."""
    res = subprocess.run(
        [
            BASH,
            "-c",
            functions(script)[WHERE] + f'\n{WHERE} "$@"',
            "--",
            *args,
        ],
        cwd=cwd,
        env={"HOME": HOME, "PATH": "/usr/bin:/bin", **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    mode, _, venv = res.stdout.rstrip("\n").partition(" ")
    return mode, venv


def test_both_bash_installers_carry_one_rule():
    defs = {s.name: functions(s).get(WHERE) for s in INSTALLERS}
    assert all(defs.values()), f"{WHERE} missing from: {defs}"
    assert defs[INSTALL_SH.name] == defs[INSTALL_DEPS.name], (
        f"{WHERE} differs between install.sh and install-deps.sh"
    )


@pytest.mark.skipif(not BASH, reason="the installers are bash scripts")
@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
@pytest.mark.parametrize("xdg", [None, "/srv/data"], ids=["home", "xdg"])
def test_the_default_venv_is_never_temporary(tmp_path, script, xdg):
    env = {"TMPDIR": str(tmp_path)}
    if xdg:
        env["XDG_DATA_HOME"] = xdg
    mode, venv = _where(script, tmp_path, env=env)
    assert mode == "user", (mode, venv)
    for base in _temp_dirs(tmp_path):
        assert not _under(venv, base), f"default venv {venv} is under {base}"
    assert venv == f"{xdg or HOME + '/.local/share'}/just-makeit/venv"


def test_install_ps1_default_is_never_temporary():
    # install.ps1 runs only under PowerShell; the Windows leg runs the
    # function itself (tests/install-ps1.windows.ps1). Here its text may
    # name no temporary folder, and names the per-user one.
    text = INSTALL_PS1.read_text(encoding="utf-8")
    m = re.search(r"^function Get-JmVenvDir \{\n(.*?)^\}", text, re.M | re.S)
    assert m, "install.ps1 defines no Get-JmVenvDir"
    # The code only: its help block and comments may say why not temp.
    code = re.sub(r"<#.*?#>", "", m.group(1), flags=re.S)
    code = "\n".join(
        ln for ln in code.splitlines() if not ln.lstrip().startswith("#")
    )
    for temp in (r"\bTEMP\b", r"\bTMP\b", "GetTempPath", "/tmp", "TMPDIR"):
        assert not re.search(temp, code, re.I), f"Get-JmVenvDir uses {temp}"
    assert "LOCALAPPDATA" in code


@pytest.mark.skipif(not BASH, reason="the installers are bash scripts")
@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
@pytest.mark.parametrize("manifest", ["pyproject.toml", "just-makeit.toml"])
def test_in_a_project_root_the_default_is_its_venv(tmp_path, script, manifest):
    (tmp_path / manifest).write_text("", encoding="utf-8")
    assert _where(script, tmp_path) == ("project", f"{tmp_path}/.venv")


@pytest.mark.skipif(not BASH, reason="the installers are bash scripts")
@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
def test_an_explicit_venv_wins_and_its_directory_decides(tmp_path, script):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Relative and with a trailing slash: absolute, the slash dropped.
    assert _where(script, elsewhere, "v/") == ("user", f"{elsewhere}/v")
    # A venv in a project root is that project's, wherever this runs.
    assert _where(script, elsewhere, str(proj / ".venv")) == (
        "project",
        f"{proj}/.venv",
    )
    # JM_VENV when no argument; the argument over JM_VENV.
    env = {"JM_VENV": str(proj / "env")}
    assert _where(script, elsewhere, env=env) == ("project", f"{proj}/env")
    assert _where(script, proj, "/opt/jm", env=env) == ("user", "/opt/jm")


#: Files that DECLARE the default, or are its history or its tests: the
#: only places a spelling may differ from the declared one.
OWNERS = re.compile(
    r"^(install\.sh|install\.ps1|src/just_makeit/scripts/install-deps\.sh"
    r"|tests/|CHANGELOG\.md|changelog\.d/)"
)

#: A spelled-out default venv path: the per-user one in any form, or the
#: temporary one it replaced.
SPELLED = re.compile(r"[^\s`'\"()]*just-makeit[/\\]venv\b|/tmp/jm-venv\b")


@pytest.mark.skipif(not BASH, reason="the installers are bash scripts")
def test_every_spelling_of_the_default_is_the_declared_one(tmp_path):
    # Registration-free: every tracked file is read, so a page that starts
    # spelling the venv's path is held the day it does. A page should not
    # need to (`install-deps --help` names it); one that does -- the
    # docs' sample installer run -- spells exactly what _jm_where says.
    _, declared = _where(INSTALL_SH, tmp_path)
    declared = "~" + declared[len(HOME) :]
    files = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    bad, seen = [], 0
    for f in files:
        if OWNERS.match(f):
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in SPELLED.finditer(line):
                seen += 1
                if m.group() != declared:
                    bad.append(f"{f}:{n}: {m.group()}")
    assert seen, "armed: the docs' sample run spells the default venv"
    assert not bad, f"spelled other than {declared}:\n  " + "\n  ".join(bad)
