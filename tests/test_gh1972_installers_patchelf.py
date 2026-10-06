"""gh-1972: both installers install patchelf on Linux, and agree per manager.

auditwheel needs patchelf to repair a Linux wheel (``just-makeit build``,
``pip wheel .``). jm's two installers disagreed about it:

1. ``install-deps.sh`` listed patchelf in every Linux ``_install_<manager>()``
   but checked only for cmake and a C compiler. On a box with both and no
   patchelf, ``--check`` printed "All build dependencies are already
   installed." and exited 0, and a run installed nothing: patchelf arrived
   only as a side effect of a missing cmake or compiler.
2. ``install.sh`` named it in no ``_install_<manager>()`` at all.

The two cannot share one list: install.sh is fetched alone by curl, before
jm exists (``tests/_installers.py``). So patchelf is a need of its own on
Linux in both, and this file holds the two copies to one another.

Every check RUNS the installers rather than reading them, under a PATH that
holds only stubs: ``uname`` answers the platform under test, cmake and a
compiler are present, patchelf is absent unless a test puts it there, sudo
runs its arguments, and every package manager records its argv and changes
nothing. just-makeit is current in the venv, so install.sh's ``--check``
verdict turns on the system alone. The interpreter that would build a venv
builds a stub one instead, so a full run is offline and writes nothing
outside ``tmp_path``.

That verdict is what found gh-1993: install.sh read the index through
``grep -oP``, and BSD grep (macOS) has no ``-P``, so there just-makeit was
never current and ``--check`` could not pass. The stub PATH carries only
the POSIX tools in `REAL_TOOLS`, and grep is not one of them, so a
GNU-only tool in either installer fails here on every host, not only on
the one that lacks it.

The per-manager comparison runs every ``_install_<manager>()`` either
script defines against those recorders, so a manager added to one script is
covered the moment it is written, and a manager one script has and the
other lacks is itself a failure.

GATE: on Linux with no patchelf, each installer's ``--check`` names it and
      exits 1, and a run hands it to the package manager; on macOS neither
      asks for it; and for every manager either script knows, the two run
      the same package-manager commands, which name patchelf on Linux.
GATE: install.sh reads just-makeit as current with no GNU-only tool, so
      its --check passes on any host with nothing to install (gh-1993).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _installers import INSTALL_SH, INSTALLERS, functions
from _installers import install_functions

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    not BASH, reason="the installers are bash scripts"
)

#: Host tools the stub PATH links unchanged: text plumbing the installers
#: pipe through, and ``id`` for their root check. Each behaves alike on
#: GNU, BSD and busybox for what the installers ask of it; grep is left out
#: because ``grep -oP`` is the GNU-only spelling gh-1993 removed.
REAL_TOOLS = ("awk", "cat", "head", "id", "sed")

#: The package managers' commands, recorded rather than run. An installer
#: that calls one not listed here finds no such command under the stub
#: PATH and fails its run, so a new manager fails loudly, never silently.
MANAGER_COMMANDS = (
    "apt-get",
    "apk",
    "brew",
    "dnf",
    "dnf5",
    "pacman",
    "zypper",
)

#: The interpreter that builds the venv, given ``-m venv DIR``: it builds a
#: stub one whose pip installs nothing and whose python answers every
#: probe. Anything else runs on the suite's own Python, so install.sh's
#: version check sees a real one.
_PYTHON_SHIM = """\
import os
import sys

args = sys.argv[1:]
if args[:2] == ["-m", "venv"]:
    bindir = os.path.join(args[-1], "bin")
    os.makedirs(bindir, exist_ok=True)
    for name, body in (("python", "echo 0"), ("pip", ":")):
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\\n" + body + "\\n")
        os.chmod(path, 0o755)
    open(os.path.join(bindir, "activate"), "w").close()
    sys.exit(0)
os.execv(sys.executable, [sys.executable] + args)
"""

#: The just-makeit both the stub venv and the stub index report.
JM_VERSION = "1.0"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


class Box:
    """A machine the installers run on, made of stubs under *root*.

    Parameters
    ----------
    root : Path
        An empty directory; the stub PATH, the call log and the venv go
        under it.
    system : str
        What ``uname`` answers: ``"Linux"`` or ``"Darwin"``.
    patchelf : bool
        Whether patchelf is on the PATH.
    """

    def __init__(self, root: Path, system: str, patchelf: bool = False):
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir(parents=True)
        self.log = root / "calls.log"
        self.log.touch()
        for tool in REAL_TOOLS:
            real = shutil.which(tool)
            assert real, f"the host has no {tool}"
            (self.bin / tool).symlink_to(real)
        _stub(self.bin / "uname", f"echo {system}")
        _stub(self.bin / "cmake", "echo cmake version 3.99.0")
        _stub(self.bin / "cc", ":")
        _stub(self.bin / "sudo", 'exec "$@"')
        if patchelf:
            _stub(self.bin / "patchelf", ":")
        # just-makeit is already current in the venv, as PyPI's index
        # reports it, so install.sh's verdict turns on the system alone.
        _stub(self.bin / "pip", f'echo "just-makeit ({JM_VERSION})"')
        venv_bin = root / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        _stub(venv_bin / "python", f"echo {JM_VERSION}")
        for cmd in MANAGER_COMMANDS:
            _stub(self.bin / cmd, f'echo "${{0##*/}} $*" >> "{self.log}"')
        shim = root / "python_shim.py"
        shim.write_text(_PYTHON_SHIM, encoding="utf-8")
        # A shell wrapper rather than a `#!` to sys.executable: a shebang
        # has a length limit, and a CI venv path can exceed it.
        self.python = root / "python3"
        _stub(self.python, f'exec "{sys.executable}" "{shim}" "$@"')

    def env(self) -> "dict[str, str]":
        """Nothing from the host's PATH: only the stubs are reachable."""
        return {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "PYTHON": str(self.python),
            "SYSTEM_PYTHON": str(self.python),
        }

    def run(self, script: Path, *args: str) -> "subprocess.CompletedProcess":
        """Run *script* on this box with *args* and a venv under it."""
        res = subprocess.run(
            [BASH, str(script), *args, str(self.root / "venv")],
            cwd=self.root,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        res.stdout = _ANSI.sub("", res.stdout)
        res.stderr = _ANSI.sub("", res.stderr)
        return res

    def calls(self) -> "list[str]":
        """Every package-manager command run so far, one argv per line."""
        return self.log.read_text(encoding="utf-8").splitlines()

    def call(self, program: str) -> "subprocess.CompletedProcess":
        """Run a bash *program* on this box, under ``set -euo pipefail``."""
        return subprocess.run(
            [BASH, "-c", "set -euo pipefail\n" + program],
            cwd=self.root,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=60,
        )


def _reported(out: str, dep: str, marks: str = "ok|-->") -> bool:
    """Whether a report line's ITEM is *dep*, under one of *marks*.

    Anchored on the line's mark and first word, never on a bare substring:
    the report prints paths, and a path may contain any word.
    """
    pattern = rf"^\s*(?:{marks})\s+{re.escape(dep)}\b"
    return re.search(pattern, out, re.M) is not None


def _marked_missing(out: str, dep: str) -> bool:
    """Whether a report marks *dep* as one it will install (``-->``)."""
    return _reported(out, dep, "-->")


def _names(calls: "list[str]", word: str) -> bool:
    return any(word in call.split() for call in calls)


def _check_passes(script: Path, res: "subprocess.CompletedProcess") -> None:
    """Assert that ``--check`` found nothing to install: exit 0.

    install.sh's verdict also asks whether just-makeit is current, so it
    must have read the stub venv's and the stub index's version as one
    (gh-1993: with ``grep -oP`` it never did on macOS).
    """
    if script == INSTALL_SH:
        assert _reported(res.stdout, "just-makeit", "ok"), (
            "install.sh did not read just-makeit as current, so its --check "
            f"cannot pass on this host (gh-1993):\n{res.stdout}{res.stderr}"
        )
    assert res.returncode == 0, (res.stdout, res.stderr)


# ── --check reports patchelf ──────────────────────────────────────────────


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
def test_check_on_linux_names_missing_patchelf(tmp_path, script):
    res = Box(tmp_path, "Linux").run(script, "--check")
    assert _marked_missing(res.stdout, "patchelf"), (
        f"{script.name} --check on Linux with no patchelf does not report "
        f"it missing; auditwheel needs it to repair a wheel (gh-1972):\n"
        f"{res.stdout}{res.stderr}"
    )
    assert res.returncode == 1, (res.stdout, res.stderr)


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
def test_check_on_linux_passes_with_patchelf(tmp_path, script):
    # The other side of the check above: armed, not merely always red.
    res = Box(tmp_path, "Linux", patchelf=True).run(script, "--check")
    assert not _marked_missing(res.stdout, "patchelf"), res.stdout
    assert _reported(res.stdout, "patchelf", "ok"), res.stdout
    _check_passes(script, res)


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
def test_check_on_macos_does_not_ask_for_patchelf(tmp_path, script):
    # delocate repairs a macOS wheel without patchelf; Homebrew is not
    # asked for it, and its absence is not a missing dependency.
    res = Box(tmp_path, "Darwin").run(script, "--check")
    assert not _reported(res.stdout, "patchelf"), res.stdout
    _check_passes(script, res)


# ── A run installs it ──────────────────────────────────────────────────────


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
def test_a_run_on_linux_installs_missing_patchelf(tmp_path, script):
    """The host's ``/etc/os-release`` picks the manager, so what is asserted
    is the installer's outcome on THIS host's distro: patchelf handed to the
    package manager it detects, or -- with none it knows -- named in the
    hint to install it by hand. Either is absent on main."""
    box = Box(tmp_path, "Linux")
    res = box.run(script)
    assert res.returncode == 0, (res.stdout, res.stderr)
    out = res.stdout + res.stderr
    hint = re.search(r"Unknown package manager\b.*\bpatchelf\b", out)
    assert _names(box.calls(), "patchelf") or hint, (
        f"{script.name} on Linux with no patchelf neither installed it nor "
        f"asked for it (gh-1972). Package-manager calls: {box.calls()}\n{out}"
    )


# ── The two installers agree, per manager ──────────────────────────────────


def _darwin_managers(tmp_path: Path) -> "set[str]":
    """The managers each installer's ``_detect_mgr`` picks on macOS."""
    found = set()
    for script in INSTALLERS:
        box = Box(tmp_path / f"detect-{script.name}", "Darwin")
        res = box.call(functions(script)["_detect_mgr"] + "\n_detect_mgr")
        assert res.returncode == 0, res.stderr
        found.add(res.stdout.strip())
    return found


def _manager_calls(tmp_path: Path, script: Path) -> "dict[str, list[str]]":
    """``{manager: calls}``: what each ``_install_<manager>()`` runs.

    Each function runs alone against the recorders, with sudo needed and
    cmake missing (``NEED_CMAKE`` is the one need a function reads, for
    Homebrew); the scripts' own ``info`` / ``warn`` lines are silenced.
    """
    out = {}
    for mgr, text in install_functions(script).items():
        box = Box(tmp_path / f"{script.name}-{mgr}", "Linux")
        res = box.call(
            f"SUDO=sudo MGR={mgr} NEED_CMAKE=1\n"
            "info() { :; }; warn() { :; }\n"
            f"{text}\n_install_{mgr}"
        )
        assert res.returncode == 0, (
            f"{script.name}'s _install_{mgr} failed under the stub PATH; a "
            f"command it runs may need a recorder in MANAGER_COMMANDS:\n"
            f"{res.stderr}"
        )
        out[mgr] = box.calls()
    return out


@pytest.fixture(scope="module")
def manager_calls(tmp_path_factory) -> "dict[str, dict[str, list[str]]]":
    root = tmp_path_factory.mktemp("gh1972-managers")
    return {s.name: _manager_calls(root, s) for s in INSTALLERS}


def test_both_installers_know_the_same_managers(manager_calls):
    known = {name: set(calls) for name, calls in manager_calls.items()}
    # Armed: apt is the manager CI's Linux legs use.
    assert all("apt" in mgrs for mgrs in known.values()), known
    assert len({frozenset(m) for m in known.values()}) == 1, (
        f"the installers support different package managers: {known}"
    )


def test_both_installers_run_the_same_commands_per_manager(manager_calls):
    by_script = list(manager_calls.items())
    (a_name, a), (b_name, b) = by_script
    differ = [
        f"{mgr}:\n  {a_name}: {a[mgr]}\n  {b_name}: {b[mgr]}"
        for mgr in sorted(set(a) & set(b))
        if a[mgr] != b[mgr]
    ]
    assert not differ, (
        "the installers install different packages for one manager; "
        "install.sh is fetched alone, so it carries its own copy, and the "
        "two must agree (gh-1972):\n" + "\n".join(differ)
    )


def test_every_linux_manager_installs_patchelf(tmp_path, manager_calls):
    darwin = _darwin_managers(tmp_path)
    assert darwin, "no installer picks a manager on macOS"
    lacking = []
    for name, calls in manager_calls.items():
        linux = sorted(set(calls) - darwin)
        assert "apt" in linux, (name, linux)
        lacking += [
            f"{name}: {m}" for m in linux if not _names(calls[m], "patchelf")
        ]
    assert not lacking, (
        "auditwheel needs patchelf to repair a Linux wheel, and these never "
        "install it (gh-1972):\n  " + "\n  ".join(lacking)
    )
