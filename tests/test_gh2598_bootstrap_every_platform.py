"""gh-2598: every ``bootstrap.toml`` jm keeps installs a toolchain everywhere.

A ``bootstrap.toml`` declares, per package manager, the system packages
``install-deps`` (just-bashit) sets up. Windows had none that worked: jm's
own file still named ``[dev.msys2]``, the UCRT64 MinGW toolchain retired by
gh-1368, and the file ``jm new`` writes had no Windows table at all. So on
Windows ``install-deps`` set up a compiler jm no longer builds with, or
nothing, while ``docs/windows.md`` listed the real prerequisites as prose.

GATE, registration-free over ``_modplatforms.PLATFORMS`` (the platforms jm
knows): both files -- jm's own and a fresh scaffold's -- carry a ``dev``
table for a package manager ``install-deps`` uses on each platform; neither
names a retired manager; and on Windows the Build Tools table requires
exactly what ``docs/windows.md`` says clang-cl needs: the C++ workload and
the Clang component. A platform added to ``PLATFORMS`` without a manager
here fails, rather than shipping a ``bootstrap.toml`` that skips it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _modplatforms

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

#: The package managers just-bashit's ``install-deps`` selects on each
#: platform. Keyed by ``_modplatforms.PLATFORMS``, so a new platform with no
#: row here is a KeyError, not a silent pass.
MANAGERS = {
    "linux": {"apt", "pacman", "dnf", "zypper", "apk"},
    "macos": {"brew"},
    "windows": {"winget"},
}

#: Managers jm no longer builds with: MinGW's UCRT64 (gh-1368).
RETIRED = {"msys2"}

BUILD_TOOLS = "Microsoft.VisualStudio.BuildTools"

#: What docs/windows.md's "What you need" lists: the MSVC libraries + SDK
#: (the C++ workload) and clang-cl (the Clang component).
VS_REQUIRES = {
    "Microsoft.VisualStudio.Workload.VCTools",
    "Microsoft.VisualStudio.Component.VC.Llvm.Clang",
}


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory):
    base = tmp_path_factory.mktemp("gh2598")
    res = run_cli("new", "proj", cwd=base)
    assert res.returncode == 0, res.stdout + res.stderr
    return base / "proj" / "bootstrap.toml"


def _files(scaffolded):
    return {
        "jm": ROOT / "bootstrap.toml",
        "scaffold": scaffolded,
    }


@pytest.mark.parametrize("which", ["jm", "scaffold"])
def test_every_platform_has_a_dev_table(scaffolded, which):
    path = _files(scaffolded)[which]
    dev = tomllib.loads(path.read_text(encoding="utf-8")).get("dev", {})
    missing = [
        p for p in _modplatforms.PLATFORMS if not MANAGERS[p] & dev.keys()
    ]
    assert not missing, f"{path}: no [dev.<manager>] for {missing}"
    retired = sorted(RETIRED & dev.keys())
    assert not retired, f"{path}: retired manager table(s) {retired}"


@pytest.mark.parametrize("which", ["jm", "scaffold"])
def test_windows_installs_clang_cl(scaffolded, which):
    path = _files(scaffolded)[which]
    winget = tomllib.loads(path.read_text(encoding="utf-8"))["dev"]["winget"]
    assert BUILD_TOOLS in winget["packages"], path
    assert set(winget[BUILD_TOOLS]["vs_requires"]) == VS_REQUIRES, path
