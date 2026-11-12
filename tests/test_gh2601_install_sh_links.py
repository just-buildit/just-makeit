"""gh-2601: install.sh run as a child links jm into ``~/.local/bin``.

``jbx just-makeit:install`` is the recommended install on every platform.
On Linux and macOS jbx sources install.sh into a throwaway ``bash -c``, so
the venv install.sh activated died with that child -- while it printed
"Venv activated -- just-makeit is ready". The same was true of ``bash
install.sh`` and of a pipe. Now, from any shell that is not interactive,
install.sh symlinks every console script the installed just-makeit
declares into ``~/.local/bin`` (where jbx's own installer puts jbx),
pointing into the venv so an upgrade is picked up, and says what is true:
ready when jm is linked and the directory is on PATH, the line to add
when it is not. A name there that is not a symlink into a jm venv is never
replaced. An interactive shell that sources the one-liner activates the
venv as before and links nothing: it asked for this session only.

Every case RUNS install.sh, offline: the venv is a real one made with
``-m venv --without-pip``, which a ``.pth`` links to the just-makeit this
suite runs (so its entry points and version are the real ones), with
console scripts written from those entry points; ``pip`` reports that
version as the latest, so install.sh finds just-makeit current and goes
straight to linking; the system tools it probes for are stubs.

GATE: a child run links every console script and ``jm --version`` runs
      from a PATH of only that directory and the system's; a file it did
      not make is left as it was and named; off PATH it prints the line to
      add and not "ready"; an interactive source activates and links
      nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from importlib.metadata import distribution
from pathlib import Path

import pytest
from _installers import INSTALL_SH

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    not BASH or sys.platform == "win32", reason="install.sh is POSIX bash"
)

#: The system directories a fresh shell's PATH holds.
SYSTEM_PATH = "/usr/bin:/bin"

#: The tools install.sh probes for, present so it installs nothing.
PROBED = ("cmake", "cc", "pkg-config", "patchelf")

_DIST = distribution("just-makeit")
VERSION = _DIST.version
#: Every console script the package declares, as install.sh reads them.
SCRIPTS = {
    e.name: e.value for e in _DIST.entry_points if e.group == "console_scripts"
}


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _jm_venv(where: Path) -> Path:
    """A venv holding this suite's just-makeit and its console scripts."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(where)],
        check=True,
    )
    py = where / "bin" / "python"
    purelib = subprocess.run(
        [
            str(py),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # The suite's own import path: just_makeit, its dist-info, tomlkit.
    here = [p for p in sys.path if p and Path(p).is_dir()]
    here.append(sysconfig.get_path("purelib"))
    Path(purelib, "jm_suite.pth").write_text(
        "\n".join(here) + "\n", encoding="utf-8"
    )
    for name, value in SCRIPTS.items():
        module, attr = value.split(":")
        code = (
            f"import sys; from {module} import {attr} as f;"
            f' sys.argv[0] = "{name}"; sys.exit(f())'
        )
        _stub(where / "bin" / name, f'exec "{py}" -c \'{code}\' "$@"')
    return where


class Machine:
    """A HOME, a jm venv and the stubs install.sh probes, under *root*."""

    def __init__(self, root: Path, venv: "Path | None" = None):
        self.home = root / "home"
        self.home.mkdir()
        self.bin = self.home / ".local" / "bin"
        self.venv = _jm_venv(venv or root / "venv")
        self.stubs = root / "stubs"
        self.stubs.mkdir()
        for tool in PROBED:
            _stub(self.stubs / tool, "echo cmake version 3.99.0")
        _stub(self.stubs / "pip", f'echo "just-makeit ({VERSION})"')

    def run(
        self,
        *argv: str,
        path: str = "",
        interactive: bool = False,
        extra: "dict[str, str] | None" = None,
        cwd: "Path | None" = None,
    ):
        """Run bash *argv* as the user would, *path* before the stubs'."""
        env = {
            **(extra or {}),
            "HOME": str(self.home),
            "PATH": ":".join(
                p for p in (path, str(self.stubs), SYSTEM_PATH) if p
            ),
            "PYTHON": sys.executable,
            "TERM": "dumb",
        }
        flags = ["-i"] if interactive else []
        res = subprocess.run(
            [BASH, *flags, *argv],
            cwd=cwd or self.home,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        res.said = res.stdout + res.stderr
        return res

    def child(self, path: str = "", extra: "dict[str, str] | None" = None):
        """``bash install.sh VENV``: install.sh as a child process."""
        return self.run(
            str(INSTALL_SH), str(self.venv), path=path, extra=extra
        )


def test_the_package_declares_jm():
    # Armed: the derived list holds the command the docs tell users to run.
    assert "jm" in SCRIPTS and "just-makeit" in SCRIPTS, SCRIPTS


def test_a_child_run_links_every_console_script(tmp_path):
    m = Machine(tmp_path)
    res = m.child()
    assert res.returncode == 0, res.said
    for name in SCRIPTS:
        link = m.bin / name
        assert link.is_symlink(), f"{name} not linked:\n{res.said}"
        assert Path(os.readlink(link)) == m.venv.resolve() / "bin" / name
    assert "Venv activated" not in res.said, res.said
    # The link itself runs jm under a PATH of only that directory and the
    # system's: nothing else -- no venv activation -- is needed. Run by
    # its path, so it is this tree's jm (the venv's .pth points at the
    # suite's just-makeit), never one found on PATH (gh-1625).
    path = f"{m.bin}:{SYSTEM_PATH}"
    run = subprocess.run(
        [str(m.bin / "jm"), "--version"],
        env={"PATH": path, "HOME": str(m.home)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == VERSION, run.stdout


def test_off_path_it_prints_the_line_to_add_and_not_ready(tmp_path):
    m = Machine(tmp_path)
    res = m.child()
    assert res.returncode == 0, res.said
    assert "is not on PATH" in res.said, res.said
    assert "jbx update-path" in res.said, res.said
    assert 'export PATH="$HOME/.local/bin:$PATH"' in res.said, res.said
    assert "is ready" not in res.said, res.said


def test_under_jbx_the_marker_puts_the_directory_on_the_callers_path(
    tmp_path,
):
    # The jbx shell function's handshake: a `path DIR` line in its reload
    # marker is put on the caller's PATH when the script exits. So jm is
    # ready in the caller, and the persisting hint is still printed.
    m = Machine(tmp_path)
    marker = tmp_path / "marker"
    marker.write_text("", encoding="utf-8")
    res = m.child(
        extra={"JB_CALLER_APPLIES": "1", "JB_RELOAD_MARKER": str(marker)}
    )
    assert res.returncode == 0, res.said
    assert marker.read_text(encoding="utf-8") == f"path {m.bin}\n"
    assert "just-makeit is ready" in res.said, res.said
    assert "jbx update-path" in res.said, res.said
    assert 'export PATH="$HOME/.local/bin:$PATH"' in res.said, res.said


def test_without_the_handshake_the_marker_is_not_written(tmp_path):
    m = Machine(tmp_path)
    marker = tmp_path / "marker"
    marker.write_text("", encoding="utf-8")
    res = m.child(extra={"JB_RELOAD_MARKER": str(marker)})
    assert res.returncode == 0, res.said
    assert marker.read_text(encoding="utf-8") == ""
    assert "is ready" not in res.said, res.said


def test_on_path_it_says_ready(tmp_path):
    m = Machine(tmp_path)
    res = m.child(path=str(m.bin))
    assert res.returncode == 0, res.said
    assert "just-makeit is ready" in res.said, res.said
    assert "is not on PATH" not in res.said, res.said


@pytest.mark.parametrize("kind", ["file", "foreign-link"])
def test_a_name_jm_did_not_make_is_left_alone(tmp_path, kind):
    m = Machine(tmp_path)
    m.bin.mkdir(parents=True)
    mine = m.bin / "jm"
    if kind == "file":
        mine.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    else:
        mine.symlink_to("/bin/true")
    before = os.readlink(mine) if kind != "file" else mine.read_text()
    res = m.child(path=str(m.bin))
    assert res.returncode == 0, res.said
    after = os.readlink(mine) if kind != "file" else mine.read_text()
    assert after == before, f"{mine} was replaced:\n{res.said}"
    assert f"{mine} " in res.said and "left as it is" in res.said, res.said
    assert "just-makeit is ready" not in res.said, res.said
    others = [n for n in SCRIPTS if n != "jm"]
    for name in others:
        assert (m.bin / name).is_symlink(), f"{name} not linked:\n{res.said}"


def test_a_link_into_another_jm_venv_is_moved_here(tmp_path):
    m = Machine(tmp_path)
    other = _jm_venv(tmp_path / "older-venv")
    m.bin.mkdir(parents=True)
    (m.bin / "jm").symlink_to(other / "bin" / "jm")
    res = m.child()
    assert res.returncode == 0, res.said
    assert Path(os.readlink(m.bin / "jm")) == m.venv.resolve() / "bin" / "jm"


def test_jbx_shape_links_and_does_not_claim_activation(tmp_path):
    # jbx runs `bash -c ". SCRIPT" -- ARGS`: sourced, in a child.
    m = Machine(tmp_path)
    res = m.run("-c", f'. "{INSTALL_SH}" "$@"', "--", str(m.venv))
    assert res.returncode == 0, res.said
    assert (m.bin / "jm").is_symlink(), res.said
    assert "Venv activated" not in res.said, res.said


def test_an_interactive_source_activates_and_links_nothing(tmp_path):
    m = Machine(tmp_path)
    res = m.run(
        "-c",
        f'. "{INSTALL_SH}" "$1"; echo "VE=$VIRTUAL_ENV"',
        "--",
        str(m.venv),
        interactive=True,
    )
    assert res.returncode == 0, res.said
    assert f"VE={m.venv}" in res.stdout, res.said
    assert "Venv activated" in res.said, res.said
    assert not m.bin.exists(), "an interactive source linked commands"


def test_a_relative_venv_is_written_absolute(tmp_path):
    # Carried from #2639: `install.sh ./venv` names the venv relative to
    # the cwd, and a link holding that relative path would resolve against
    # ~/.local/bin instead. Every link is absolute, into the venv.
    m = Machine(tmp_path)
    res = m.run(str(INSTALL_SH), "--", "./venv", cwd=tmp_path)
    assert res.returncode == 0, res.said
    # Compared as text: Path() would fold a `./` the link still holds.
    target = os.readlink(m.bin / "jm")
    assert target == str(m.venv.resolve() / "bin" / "jm"), target


def test_a_link_into_a_venv_that_is_gone_is_replaced(tmp_path):
    # The recovery path: a reboot emptied the /tmp the old default venv
    # lived in, so ~/.local/bin/jm dangles. A re-run must replace it, not
    # refuse it as "not a jm venv" because its python no longer runs.
    m = Machine(tmp_path)
    m.bin.mkdir(parents=True)
    gone = tmp_path / "gone" / "jm-venv" / "bin" / "jm"
    (m.bin / "jm").symlink_to(gone)
    res = m.child()
    assert res.returncode == 0, res.said
    assert Path(os.readlink(m.bin / "jm")) == m.venv.resolve() / "bin" / "jm"
    assert "left as it is" not in res.said, res.said


def test_a_dangling_link_of_another_shape_is_left_alone(tmp_path):
    # Only a link of jm's own shape, <venv>/bin/<name>, is taken back: a
    # dangling link the user made to some other name is still theirs.
    m = Machine(tmp_path)
    m.bin.mkdir(parents=True)
    theirs = tmp_path / "gone" / "my-jm-wrapper"
    (m.bin / "jm").symlink_to(theirs)
    res = m.child(path=str(m.bin))
    assert res.returncode == 0, res.said
    assert os.readlink(m.bin / "jm") == str(theirs)
    assert "left as it is" in res.said, res.said


def test_a_relative_link_into_a_jm_venv_is_moved_here(tmp_path):
    # A relative link target is relative to ~/.local/bin, not to the cwd
    # install.sh runs in; read against the cwd, jm's own link was refused.
    m = Machine(tmp_path)
    other = _jm_venv(tmp_path / "older-venv")
    m.bin.mkdir(parents=True)
    rel = os.path.relpath(other / "bin" / "jm", m.bin)
    (m.bin / "jm").symlink_to(rel)
    # A cwd from which the same relative path names something that is
    # there but is NOT a jm venv, so a cwd-relative reading refuses it.
    cwd = m.home / "a" / "b" / "c"
    cwd.mkdir(parents=True)
    decoy = (cwd / rel).parent
    decoy.mkdir(parents=True)
    _stub(decoy / "jm", "echo decoy")
    _stub(decoy / "python", "exit 1")
    res = m.run(str(INSTALL_SH), str(m.venv), cwd=cwd)
    assert res.returncode == 0, res.said
    assert Path(os.readlink(m.bin / "jm")) == m.venv.resolve() / "bin" / "jm"


def test_an_unwritable_marker_is_named_and_not_claimed(tmp_path):
    m = Machine(tmp_path)
    marker = tmp_path / "no-such-dir" / "marker"
    res = m.child(
        extra={"JB_CALLER_APPLIES": "1", "JB_RELOAD_MARKER": str(marker)}
    )
    assert res.returncode == 0, res.said
    assert "could not write jbx's reload marker" in res.said, res.said
    assert "is ready" not in res.said, res.said
    assert "is not on PATH" in res.said, res.said


def test_the_default_venv_is_the_persistent_one_and_links_into_it(tmp_path):
    # No path given, outside a project: the per-user venv under the XDG
    # data directory, and every link resolves into it.
    home = tmp_path / "home"
    m = Machine(
        tmp_path, venv=home / ".local" / "share" / "just-makeit" / "venv"
    )
    res = m.run(str(INSTALL_SH), cwd=tmp_path)
    assert res.returncode == 0, res.said
    for name in SCRIPTS:
        assert (m.bin / name).resolve() == (m.venv / "bin" / name).resolve()


def _project(tmp_path: Path, manifest: str) -> "tuple[Machine, Path]":
    """A Machine whose venv is the .venv of a project root holding *manifest*."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / manifest).write_text("", encoding="utf-8")
    return Machine(tmp_path, venv=proj / ".venv"), proj


@pytest.mark.parametrize("manifest", ["pyproject.toml", "just-makeit.toml"])
def test_in_a_project_root_the_projects_venv_and_no_link(tmp_path, manifest):
    m, proj = _project(tmp_path, manifest)
    res = m.run(str(INSTALL_SH), cwd=proj)
    assert res.returncode == 0, res.said
    assert not m.bin.exists(), "a project install linked into ~/.local/bin"
    assert f"{m.venv.resolve()}/bin/activate" in res.said, res.said
    assert "jbx update-path" not in res.said, res.said


def test_in_a_project_root_jbx_puts_its_venv_on_path(tmp_path):
    m, proj = _project(tmp_path, "pyproject.toml")
    marker = tmp_path / "marker"
    res = m.run(
        "-c",
        f'. "{INSTALL_SH}" "$@"',
        "--",
        cwd=proj,
        extra={"JB_CALLER_APPLIES": "1", "JB_RELOAD_MARKER": str(marker)},
    )
    assert res.returncode == 0, res.said
    assert marker.read_text(encoding="utf-8") == (
        f"path {m.venv.resolve()}/bin\n"
    )
    assert not m.bin.exists(), res.said
    assert "just-makeit is ready" in res.said, res.said


def test_an_explicit_venv_in_a_project_root_is_the_projects(tmp_path):
    # The rule for a path argument: the directory holding the venv decides.
    m, _ = _project(tmp_path, "pyproject.toml")
    res = m.child()
    assert res.returncode == 0, res.said
    assert not m.bin.exists(), res.said
